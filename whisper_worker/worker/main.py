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


# Two sources, two ways of naming the audio on disk.
#
# Telegram reconstructs the filename from (chat_id, source_message_id), which
# works because both are integers. WhatsApp does NOT: its chat keys are text
# containing '@', '.' and '-', so the bridge stores the absolute path at
# ingest and that column is the contract. Do not "unify" these by rebuilding
# the WhatsApp path from parts.
#
# Note the raw_source predicate on BOTH arms. It was previously absent on the
# Telegram join, which was safe only by accident: raw_id is an unFK'd pointer
# and the channel filter happened to imply which table it pointed into. With a
# second raw table whose BIGSERIAL overlaps, the unqualified join silently
# matches the wrong row.
_PENDING_SQL = """
SELECT interaction_id, audio_path FROM (
    SELECT i.id::text AS interaction_id,
           $1::text || '/' || r.chat_id::text || '_'
                    || r.source_message_id::text || '.ogg' AS audio_path
      FROM canonical.interaction i
      JOIN raw.telegram_message r ON r.id = i.raw_id
     WHERE i.channel = 'telegram_voice'
       AND i.raw_source = 'raw.telegram_message'
       AND i.body IS NULL
    UNION ALL
    SELECT i.id::text AS interaction_id,
           w.voice_file_path AS audio_path
      FROM canonical.interaction i
      JOIN raw.whatsapp_message w ON w.id = i.raw_id
     WHERE i.channel = 'whatsapp_voice'
       AND i.raw_source = 'raw.whatsapp_message'
       AND i.body IS NULL
       AND w.voice_file_path IS NOT NULL
) pending
WHERE ($2::text IS NULL OR interaction_id > $2)
ORDER BY interaction_id
LIMIT $3
"""


async def _fetch_pending(
    pool: asyncpg.Pool, *, voice_dir: str, after_id: str | None, limit: int
) -> list[asyncpg.Record]:
    """Untranscribed voice interactions with the path to their audio, ordered
    by interaction id ascending. Skips rows whose file is missing from disk —
    for Telegram the download-voice fetcher subcommand fills those in; for
    WhatsApp the media had already expired when we tried."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(_PENDING_SQL, voice_dir, after_id, limit)
    return [r for r in rows if r["audio_path"] and Path(r["audio_path"]).exists()]


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
                file_path = r["audio_path"]
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
