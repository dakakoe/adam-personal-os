from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import asyncpg
from telethon import TelegramClient

from .config import Config

log = logging.getLogger(__name__)


async def run(client: TelegramClient, pool: asyncpg.Pool, cfg: Config) -> None:
    """One-shot: walk raw.telegram_message for voice rows whose .ogg files
    aren't on disk yet (typically: ingested while TELETHON_DOWNLOAD_VOICE=false)
    and download them. Idempotent — re-runs skip already-present files."""
    voice_root = Path(cfg.voice_dir)
    voice_root.mkdir(parents=True, exist_ok=True)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, chat_id, source_message_id
            FROM raw.telegram_message
            WHERE kind = 'voice'
            ORDER BY chat_id, source_message_id
            """
        )

    by_chat: dict[int, list[asyncpg.Record]] = defaultdict(list)
    needed = 0
    skipped = 0
    for r in rows:
        out = voice_root / f"{r['chat_id']}_{r['source_message_id']}.ogg"
        if out.exists():
            skipped += 1
            continue
        by_chat[r["chat_id"]].append(r)
        needed += 1

    log.info(
        "download-voice: %d voice rows total, %d already on disk, %d to fetch (%d chats)",
        len(rows),
        skipped,
        needed,
        len(by_chat),
    )

    fetched = 0
    failed = 0
    CHUNK = 100  # Telegram's GetMessages cap per call
    for chat_id, group in by_chat.items():
        try:
            entity = await client.get_input_entity(chat_id)
        except Exception:
            log.exception("could not resolve entity for chat_id=%d (skipping %d msgs)",
                          chat_id, len(group))
            failed += len(group)
            continue

        for offset in range(0, len(group), CHUNK):
            chunk = group[offset : offset + CHUNK]
            ids = [r["source_message_id"] for r in chunk]
            # get_messages returns one entry per id, in the same order, with
            # None for ids the server can't return (deleted, restricted, etc.).
            msgs = await client.get_messages(entity, ids=ids)
            for r, msg in zip(chunk, msgs):
                out = voice_root / f"{chat_id}_{r['source_message_id']}.ogg"
                if msg is None or msg.voice is None:
                    log.warning(
                        "chat=%d msg=%d: no voice on returned message (deleted?)",
                        chat_id, r["source_message_id"],
                    )
                    failed += 1
                    continue
                try:
                    await client.download_media(msg, file=str(out))
                    fetched += 1
                    if fetched % 50 == 0:
                        log.info("progress: fetched=%d failed=%d (of %d)",
                                 fetched, failed, needed)
                except Exception:
                    log.exception("download failed for chat=%d msg=%d",
                                  chat_id, r["source_message_id"])
                    failed += 1

    log.info("download-voice done: fetched=%d failed=%d skipped=%d",
             fetched, failed, skipped)
