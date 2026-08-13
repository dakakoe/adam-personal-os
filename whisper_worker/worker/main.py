from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import asyncpg

from .config import Config
from . import transcribe

log = logging.getLogger(__name__)

# Whisper returns an empty string for clips with no speech (silent / 1-tap
# / accidentally-sent voice notes < 1s). Without a sentinel, those rows stay
# body=NULL forever and the worker re-attempts them every poll cycle.
SILENT_MARKER = "[no speech]"


async def _fetch_pending(
    pool: asyncpg.Pool, *, voice_dir: str, after_id: str | None, limit: int
) -> list[asyncpg.Record]:
    """Untranscribed voice interactions, ordered by interaction id ascending.
    Joins to raw.telegram_message to recover the (chat_id, source_message_id)
    that names the .ogg on disk. Skips rows whose audio file is missing —
    the download-voice fetcher subcommand fills those in."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT i.id::text AS interaction_id,
                   r.chat_id,
                   r.source_message_id
            FROM canonical.interaction i
            JOIN raw.telegram_message r ON r.id = i.raw_id
            WHERE i.channel = 'telegram_voice'
              AND i.body IS NULL
              AND ($1::text IS NULL OR i.id::text > $1)
            ORDER BY i.id
            LIMIT $2
            """,
            after_id,
            limit,
        )
    out: list[asyncpg.Record] = []
    for r in rows:
        p = Path(voice_dir) / f"{r['chat_id']}_{r['source_message_id']}.ogg"
        if p.exists():
            out.append(r)
    return out


async def _write_transcript(
    pool: asyncpg.Pool, interaction_id: str, transcript: str
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE canonical.interaction
            SET body = $2
            WHERE id = $1::uuid
            """,
            interaction_id,
            transcript,
        )


async def run(cfg: Config) -> None:
    pool = await asyncpg.create_pool(
        cfg.db_url, min_size=1, max_size=3, statement_cache_size=0
    )
    model = transcribe.load_model(cfg)
    log.info(
        "whisper worker ready: batch=%d poll=%ds language=%s",
        cfg.batch_size, cfg.poll_interval_sec, cfg.language or "auto",
    )

    after_id: str | None = None
    transcribed = 0
    failed = 0

    try:
        while True:
            batch = await _fetch_pending(
                pool, voice_dir=cfg.voice_dir,
                after_id=after_id, limit=cfg.batch_size,
            )
            if not batch:
                if after_id is not None:
                    log.info(
                        "queue drained this pass: transcribed=%d failed=%d session totals",
                        transcribed, failed,
                    )
                after_id = None  # next poll starts from the top
                await asyncio.sleep(cfg.poll_interval_sec)
                continue

            for r in batch:
                file_path = (
                    f"{cfg.voice_dir}/{r['chat_id']}_{r['source_message_id']}.ogg"
                )
                try:
                    text, lang = await transcribe.transcribe_file(
                        model, file_path, cfg.language
                    )
                    if text:
                        await _write_transcript(pool, r["interaction_id"], text)
                        transcribed += 1
                        if transcribed % 25 == 0:
                            log.info(
                                "progress: transcribed=%d failed=%d last_lang=%s",
                                transcribed, failed, lang,
                            )
                    else:
                        # Empty transcript == no speech in the clip (silent /
                        # accidental / sub-second tap). Mark with sentinel so
                        # we don't re-attempt forever. NULL stays reserved
                        # for 'transcription not yet attempted'.
                        await _write_transcript(
                            pool, r["interaction_id"], SILENT_MARKER
                        )
                        log.info(
                            "no speech in %s (path=%s lang=%s); marked silent",
                            r["interaction_id"], file_path, lang,
                        )
                except Exception:
                    log.exception(
                        "transcription failed for %s (path=%s)",
                        r["interaction_id"], file_path,
                    )
                    failed += 1
                finally:
                    # Advance cursor regardless of success so a broken file
                    # can't trap us in a tight retry loop. Re-run the worker
                    # to retry rows whose body is still NULL.
                    after_id = r["interaction_id"]
    finally:
        await pool.close()
        log.info("shutting down: transcribed=%d failed=%d total",
                 transcribed, failed)
