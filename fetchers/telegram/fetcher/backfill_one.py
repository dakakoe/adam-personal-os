"""Backfill messages from a single Telegram chat (resolved by chat_id).

Powered by Telethon's iter_messages — same engine the corpus-wide
backfill uses, just scoped to one entity. Used by the merge_ui
"Backfill history" button per enabled group.

Like enrich-bios and discover-groups, this opens a temporary session
copy because the live memory-telethon service holds the SQLite write
lock on the real session file.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
from telethon import TelegramClient

from . import raw
from .config import Config

log = logging.getLogger(__name__)


async def run(
    client: TelegramClient,
    pool: asyncpg.Pool,
    cfg: Config,
    *,
    chat_id: int,
    since_days: int | None = None,
) -> dict[str, int]:
    """Walk msg history newest-first. `since_days` caps how far back we
    go — None means full history (may be many thousands for active
    groups). Returns {seen, new} counts."""
    entity = await client.get_entity(chat_id)
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=since_days)
        if since_days is not None
        else None
    )
    await raw.record_chat(pool, entity)

    seen = 0
    new = 0
    async for msg in client.iter_messages(entity, offset_date=None, reverse=False):
        if cutoff is not None and msg.date < cutoff:
            break
        seen += 1
        try:
            inserted = await raw.record_message(
                pool,
                client,
                msg,
                voice_dir=cfg.voice_dir,
                download_voice=cfg.download_voice,
            )
            if inserted:
                new += 1
        except Exception:
            log.exception("backfill_one: failed on msg %s in chat %s", msg.id, chat_id)

        if seen % 200 == 0:
            log.info(
                "backfill_one: chat=%s progress seen=%d new=%d", chat_id, seen, new
            )

    log.info(
        "backfill_one: chat=%s done seen=%d new=%d (since_days=%s)",
        chat_id, seen, new, since_days,
    )
    return {"seen": seen, "new": new}
