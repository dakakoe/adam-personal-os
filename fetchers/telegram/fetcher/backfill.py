from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import asyncpg
from telethon import TelegramClient
from telethon.tl import types

from . import raw
from .config import Config

log = logging.getLogger(__name__)


async def run(client: TelegramClient, pool: asyncpg.Pool, cfg: Config) -> None:
    if cfg.backfill_days <= 0:
        cutoff = None
        window_desc = "no cutoff (walk to first message)"
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.backfill_days)
        window_desc = f"{cutoff.isoformat(timespec='seconds')}..now"
    log.info(
        "backfill: filter=%s download_voice=%s window=%s",
        cfg.chat_filter,
        cfg.download_voice,
        window_desc,
    )

    dialog_count = 0
    msg_count = 0
    new_count = 0

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        is_private = isinstance(entity, types.User)

        # Group / channel ingestion is opt-in via raw.telegram_group_allowlist.
        # Always record the group's presence so the UI can list it; skip
        # its messages unless enabled. cfg.chat_filter is legacy; the
        # allowlist is the source of truth for groups.
        if not is_private:
            last_msg = getattr(dialog, "date", None)
            await raw.upsert_group_allowlist_row(
                pool, entity, last_message_at=last_msg,
            )
            # Pass the entity so is_group_enabled normalizes to signed.
            if not await raw.is_group_enabled(pool, entity):
                continue

        dialog_count += 1
        await raw.record_chat(pool, entity)
        if is_private:
            await raw.record_user(pool, entity)

        dialog_new = 0
        dialog_seen = 0
        async for msg in client.iter_messages(entity, offset_date=None, reverse=False):
            if cutoff is not None and msg.date < cutoff:
                break  # iter_messages yields newest first; once we cross the cutoff we're done
            msg_count += 1
            dialog_seen += 1
            try:
                inserted = await raw.record_message(
                    pool,
                    client,
                    msg,
                    voice_dir=cfg.voice_dir,
                    download_voice=cfg.download_voice,
                )
                if inserted:
                    new_count += 1
                    dialog_new += 1
            except Exception:
                log.exception(
                    "failed on message %s in %s", msg.id, _entity_label(entity)
                )

        # Per-dialog summary so we can tail the log and follow progress
        # without one line per (dialog, message) when chats are huge.
        log.info(
            "dialog %s (%s) seen=%d new=%d totals: dialogs=%d msgs=%d new=%d",
            dialog.name or "<no name>",
            _entity_label(entity),
            dialog_seen,
            dialog_new,
            dialog_count,
            msg_count,
            new_count,
        )

    log.info(
        "backfill done: dialogs=%d messages_seen=%d new_rows=%d",
        dialog_count,
        msg_count,
        new_count,
    )


def _entity_label(entity) -> str:
    if isinstance(entity, types.User):
        name = " ".join(filter(None, [entity.first_name, entity.last_name])) or entity.username or "user"
        return f"user:{entity.id} ({name})"
    if isinstance(entity, (types.Chat, types.Channel)):
        return f"chat:{entity.id} ({getattr(entity, 'title', '?')})"
    return repr(entity)
