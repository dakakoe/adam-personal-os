from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import asyncpg
from telethon import TelegramClient, utils as tg_utils
from telethon.tl import types

from . import db


def _peer_id_signed(entity: Any) -> int:
    """Canonical signed Telegram peer ID. Matches the form used by
    msg.chat_id (e.g. -1002478596513 for supergroups, -N for legacy
    groups). raw.telegram_message stores chat_id in this form, so
    using the same form in the allowlist makes JOINs work."""
    return int(tg_utils.get_peer_id(entity))

log = logging.getLogger(__name__)


def _entity_kind(entity: Any) -> str:
    if isinstance(entity, types.User):
        return "private"
    if isinstance(entity, types.Chat):
        return "group"
    if isinstance(entity, types.Channel):
        return "supergroup" if entity.megagroup else "channel"
    return "other"


def _msg_kind(msg: types.Message) -> str:
    if msg.voice:
        return "voice"
    if msg.photo:
        return "photo"
    if msg.video:
        return "video"
    if msg.document:
        return "document"
    if msg.sticker:
        return "sticker"
    if msg.text:
        return "text"
    return "other"


def _to_dict(obj: Any) -> dict:
    """Telethon objects expose .to_dict(); for anything else, str-coerce."""
    if obj is None:
        return {}
    try:
        return obj.to_dict()
    except AttributeError:
        return {"_repr": repr(obj)}


async def record_user(pool: asyncpg.Pool, user: types.User) -> None:
    await db.upsert_user(
        pool,
        source_user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        phone=user.phone,
        is_bot=bool(user.bot),
        payload=_to_dict(user),
    )


async def upsert_group_allowlist_row(
    pool: asyncpg.Pool,
    entity: Any,
    *,
    last_message_at: Any = None,
) -> None:
    """Mark this group/supergroup/channel as 'seen' in the allowlist.
    Idempotent — ON CONFLICT updates title + member_count + last_seen_at
    so the UI list stays fresh, but never flips enabled. Default
    enabled=FALSE means the live/backfill code will skip messages here
    until the user opts in via /api/telegram/groups/{chat_id}/toggle.

    `last_message_at` (Telethon datetime) is the chat's OWN last-message
    timestamp — separate from `last_seen_at` (when WE last touched the
    row). Only ever moves forward (GREATEST below) so a stale ingest
    doesn't overwrite newer data."""
    # member_count attribute lives in different places across entity types;
    # be defensive — newer Telethon versions may rename.
    member_count = (
        getattr(entity, "participants_count", None)
        or getattr(entity, "member_count", None)
    )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO raw.telegram_group_allowlist
              (chat_id, title, kind, member_count, last_seen_at, last_message_at)
            VALUES ($1, $2, $3, $4, now(), $5)
            ON CONFLICT (chat_id) DO UPDATE SET
              title           = EXCLUDED.title,
              kind            = EXCLUDED.kind,
              member_count    = COALESCE(EXCLUDED.member_count, raw.telegram_group_allowlist.member_count),
              last_seen_at    = now(),
              last_message_at = GREATEST(
                                  raw.telegram_group_allowlist.last_message_at,
                                  EXCLUDED.last_message_at
                                )
            """,
            _peer_id_signed(entity),
            getattr(entity, "title", None),
            _entity_kind(entity),
            member_count,
            last_message_at,
        )


async def is_group_enabled(pool: asyncpg.Pool, chat_id_or_entity: Any) -> bool:
    """Hot-path lookup for live ingest. Accepts either a raw msg.chat_id
    (already signed) or a Telethon entity (we normalize to signed via
    get_peer_id). Either way matches the allowlist's storage form."""
    if isinstance(chat_id_or_entity, int):
        chat_id = chat_id_or_entity
    else:
        chat_id = _peer_id_signed(chat_id_or_entity)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT enabled FROM raw.telegram_group_allowlist WHERE chat_id = $1",
            chat_id,
        )
    return bool(row and row["enabled"])


async def record_chat(pool: asyncpg.Pool, entity: Any) -> None:
    if isinstance(entity, types.User):
        # private chats are keyed on the counterparty's user_id
        await db.upsert_chat(
            pool,
            source_chat_id=entity.id,
            kind="private",
            title=None,
            username=entity.username,
            payload=_to_dict(entity),
        )
        return
    await db.upsert_chat(
        pool,
        source_chat_id=entity.id,
        kind=_entity_kind(entity),
        title=getattr(entity, "title", None),
        username=getattr(entity, "username", None),
        payload=_to_dict(entity),
    )


async def record_message(
    pool: asyncpg.Pool,
    client: TelegramClient,
    msg: types.Message,
    *,
    voice_dir: str,
    download_voice: bool = True,
) -> bool:
    sender_id = msg.sender_id if msg.sender_id else None
    chat_id = msg.chat_id

    kind = _msg_kind(msg)
    voice_path: str | None = None
    if kind == "voice" and download_voice:
        Path(voice_dir).mkdir(parents=True, exist_ok=True)
        # Stable filename per (chat, message) so re-runs overwrite cleanly.
        out = Path(voice_dir) / f"{chat_id}_{msg.id}.ogg"
        if not out.exists():
            try:
                await client.download_media(msg, file=str(out))
            except Exception as e:
                log.warning("failed to download voice %s/%s: %s", chat_id, msg.id, e)
                out = None
        voice_path = str(out) if out and out.exists() else None

    return await db.insert_message(
        pool,
        chat_id=chat_id,
        source_message_id=msg.id,
        sender_id=sender_id,
        message_date=msg.date,
        kind=kind,
        text=msg.text or None,
        voice_file_path=voice_path,
        payload=_to_dict(msg),
    )
