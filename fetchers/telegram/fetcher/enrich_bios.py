"""One-shot pass that calls GetFullUser on every contact we haven't fetched
yet (or that's overdue) and caches the `about` bio into raw.telegram_user.

Telegram's flood-wait limit kicks in well before we hit it at 1 req/sec.
Telethon raises FloodWaitError on its own — we wait the requested duration
then retry. Resumable: re-runs only touch users with NULL/old
last_full_fetch_at.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import asyncpg
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.tl import types
from telethon.tl.functions.users import GetFullUserRequest

from .config import Config

log = logging.getLogger(__name__)


# Sleep between successful GetFullUser calls. 0.4s = ~150/min, well under
# Telegram's documented limits for User RPCs and friendly to our session.
_PACE_SECONDS = 0.4

# Re-fetch users whose bio cache is older than this many days. NULL always
# refetches.
_REFRESH_DAYS = 30


async def _pending(pool: asyncpg.Pool, limit: int | None) -> list[dict[str, Any]]:
    """Pick contacts that need a fresh full-user fetch. Sorted busiest-first
    so a partial run still hits the most useful contacts."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_REFRESH_DAYS)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.source_user_id, u.username
            FROM raw.telegram_user u
            JOIN canonical.identity i
              ON i.source = 'telegram' AND i.source_id::bigint = u.source_user_id
            JOIN canonical.person p
              ON p.id = i.person_id AND p.merged_into IS NULL
            WHERE u.is_bot = false
              AND (u.last_full_fetch_at IS NULL OR u.last_full_fetch_at < $1)
            ORDER BY (SELECT count(*) FROM canonical.interaction WHERE person_id = p.id) DESC
            """
            + (" LIMIT $2" if limit else ""),
            *([cutoff, limit] if limit else [cutoff]),
        )
    return [{"source_user_id": r["source_user_id"], "username": r["username"]}
            for r in rows]


async def _save(pool: asyncpg.Pool, user_id: int, about: str | None, birthday=None) -> None:
    """Persist the bio text (+ birthday when present) and bump
    last_full_fetch_at. Empty bios store as NULL (so dashboards can count them
    separately from unfetched), but last_full_fetch_at is still set so we don't
    re-fetch them every run. Birthday uses COALESCE so a transient privacy
    change (None this run) never wipes a previously-captured value."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE raw.telegram_user
               SET about              = $2,
                   birthday           = COALESCE($3, birthday),
                   last_full_fetch_at = now()
             WHERE source_user_id = $1
            """,
            user_id,
            about if about else None,
            birthday,
        )


async def run(client: TelegramClient, pool: asyncpg.Pool, cfg: Config) -> None:
    # Local override knob so the operator can run a small smoke test:
    # ENRICH_BIOS_LIMIT=10 fetcher enrich-bios
    import os
    limit_env = os.environ.get("ENRICH_BIOS_LIMIT")
    limit = int(limit_env) if limit_env else None

    pending = await _pending(pool, limit)
    log.info(
        "enrich-bios: %d contacts to fetch (limit=%s, refresh_after=%dd)",
        len(pending), limit, _REFRESH_DAYS,
    )

    fetched = with_bio = empty = failed = with_bday = 0
    for entry in pending:
        uid = entry["source_user_id"]
        try:
            full = await client(GetFullUserRequest(id=uid))
            about = _extract_about(full)
            bday = _extract_birthday(full)
            await _save(pool, uid, about, bday)
            fetched += 1
            if about:
                with_bio += 1
            else:
                empty += 1
            if bday:
                with_bday += 1
        except FloodWaitError as e:
            log.warning("flood wait %ds on user %s; sleeping", e.seconds, uid)
            await asyncio.sleep(e.seconds + 1)
            # Don't count this attempt — loop continues to the next entry.
        except RPCError as e:
            # Most common: USER_INVALID_HANDLE, CHANNEL_PRIVATE for deleted
            # accounts. Log and skip; we don't need to retry these.
            log.warning("getFullUser failed for %s (@%s): %s",
                        uid, entry["username"], e)
            failed += 1
            await _save(pool, uid, None)  # mark as fetched so we skip next run
        except Exception:
            log.exception("unexpected error on user %s", uid)
            failed += 1

        if fetched % 100 == 0 and fetched > 0:
            log.info("progress: fetched=%d with_bio=%d empty=%d failed=%d",
                     fetched, with_bio, empty, failed)

        await asyncio.sleep(_PACE_SECONDS)

    log.info("enrich-bios done: fetched=%d with_bio=%d with_bday=%d empty=%d failed=%d",
             fetched, with_bio, with_bday, empty, failed)


def _extract_about(full: Any) -> str | None:
    """`GetFullUserRequest` returns a `UserFull` envelope. Telethon's shape
    changed across versions — probe the documented attribute first, then
    fall back to scanning nested objects."""
    # Modern telethon: full.full_user.about
    fu = getattr(full, "full_user", None)
    if fu is not None:
        about = getattr(fu, "about", None)
        if about is not None:
            return about
    # Legacy: full.about directly
    about = getattr(full, "about", None)
    return about


def _extract_birthday(full: Any) -> date | None:
    """Telegram profile birthday (UserFull.birthday, a types.Birthday with
    day/month and an optional year), 2024+. Only present when the contact set
    one AND their privacy lets us see it. Year withheld → 1900 sentinel, same
    convention as the Google-contacts path."""
    fu = getattr(full, "full_user", None) or full
    b = getattr(fu, "birthday", None)
    if b is None:
        return None
    day = getattr(b, "day", None)
    month = getattr(b, "month", None)
    year = getattr(b, "year", None) or 1900
    if not day or not month:
        return None
    try:
        return date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None
