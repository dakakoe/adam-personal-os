"""Download profile photos for every Telegram user we know about, into
/srv/memory/data/avatars/tg_<source_user_id>.jpg, and record the local path
in memory.person_photo so the merge_api /photo endpoint can serve them.

Telegram doesn't expose stable public photo URLs (file references are
encrypted), so the bytes must come down through Telethon's
download_profile_photo. The files are small (avatars are downscaled
JPEGs, typically <100KB), so storing all ~3k of them costs <500MB on disk.

Same session-copy trick as enrich_bios — the live ingest already holds a
write lock on the SQLite session file, so we work off a copy.

Resumable: by default only fetches users that don't yet have a row in
memory.person_photo with source='telegram'. Set DOWNLOAD_AVATARS_FORCE=1
to refetch everyone (e.g. when users have changed their avatars).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import asyncpg
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError

from .config import Config

log = logging.getLogger(__name__)


# Pace at ~120/min — heavier RPC than GetFullUser since bytes come down.
# 2,933 users / 120/min = ~25 min wall-clock for the first backfill.
_PACE_SECONDS = 0.5


def _avatars_dir() -> Path:
    p = Path(os.environ.get("AVATARS_DIR", "/srv/memory/data/avatars"))
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _pending(pool: asyncpg.Pool, *, force: bool, limit: int | None) -> list[dict[str, Any]]:
    """Telegram users mapped to a non-merged person who either don't have a
    photo row yet (default) or all of them (force). Sorted by interaction
    volume so a partial run hits the most useful contacts first."""
    sql = """
        SELECT u.source_user_id, p.id::text AS person_id
        FROM raw.telegram_user u
        JOIN canonical.identity i
          ON i.source = 'telegram' AND i.source_id::bigint = u.source_user_id
        JOIN canonical.person p
          ON p.id = i.person_id AND p.merged_into IS NULL
        WHERE u.is_bot = false
        """
    if not force:
        sql += """
          AND NOT EXISTS (
            SELECT 1 FROM memory.person_photo pp
             WHERE pp.person_id = p.id AND pp.source = 'telegram'
          )
        """
    sql += """
        ORDER BY (SELECT count(*) FROM canonical.interaction WHERE person_id = p.id) DESC
        """
    if limit:
        sql += " LIMIT $1"
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, limit)
    else:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql)
    return [dict(r) for r in rows]


async def _save(pool: asyncpg.Pool, person_id: str, local_path: str) -> None:
    """Upsert the photo row. Telegram wins over google_contacts (the
    endpoint precedence already prefers local files anyway, but this
    keeps the table semantically correct after a force-refresh)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO memory.person_photo
              (person_id, source, local_path, fetched_at)
            VALUES ($1::uuid, 'telegram', $2, now())
            ON CONFLICT (person_id) DO UPDATE SET
              source = 'telegram',
              local_path = EXCLUDED.local_path,
              url = NULL,
              fetched_at = now()
            """,
            person_id, local_path,
        )


async def run(client: TelegramClient, pool: asyncpg.Pool, cfg: Config) -> None:
    force = os.environ.get("DOWNLOAD_AVATARS_FORCE", "").strip() not in ("", "0", "false", "no")
    limit_env = os.environ.get("DOWNLOAD_AVATARS_LIMIT")
    limit = int(limit_env) if limit_env else None

    avatars = _avatars_dir()
    pending = await _pending(pool, force=force, limit=limit)
    log.info(
        "download-avatars: %d users to process (force=%s limit=%s dir=%s)",
        len(pending), force, limit, avatars,
    )

    downloaded = no_photo = failed = skipped_existing = 0
    for entry in pending:
        uid = entry["source_user_id"]
        person_id = entry["person_id"]
        target = avatars / f"tg_{uid}.jpg"

        # If the file already exists and we're not forcing, the only reason
        # to be here is a missing person_photo row — just record the path.
        if target.exists() and not force:
            await _save(pool, person_id, str(target))
            skipped_existing += 1
            await asyncio.sleep(0)  # cooperative yield
            continue

        try:
            # download_profile_photo returns the path it wrote to, or None
            # if the user has no photo (or it's hidden by their privacy).
            result = await client.download_profile_photo(uid, file=str(target))
            if result is None:
                no_photo += 1
            else:
                # Telethon may append an extension if we omitted one, but we
                # passed the full filename so result == target. Defensive:
                # trust the returned path.
                await _save(pool, person_id, str(result))
                downloaded += 1
        except FloodWaitError as e:
            log.warning("flood wait %ds on user %s; sleeping", e.seconds, uid)
            await asyncio.sleep(e.seconds + 1)
            # Don't count — loop continues to the next entry.
        except RPCError as e:
            log.warning("download_profile_photo failed for %s: %s", uid, e)
            failed += 1
        except Exception:
            log.exception("unexpected error on user %s", uid)
            failed += 1

        total = downloaded + no_photo + failed + skipped_existing
        if total % 100 == 0 and total > 0:
            log.info(
                "progress: downloaded=%d no_photo=%d failed=%d skipped_existing=%d",
                downloaded, no_photo, failed, skipped_existing,
            )

        await asyncio.sleep(_PACE_SECONDS)

    log.info(
        "download-avatars done: downloaded=%d no_photo=%d failed=%d skipped_existing=%d",
        downloaded, no_photo, failed, skipped_existing,
    )
