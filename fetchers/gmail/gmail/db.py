from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

log = logging.getLogger(__name__)


async def connect(db_url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        db_url, min_size=1, max_size=2, statement_cache_size=0,
    )


async def list_active_accounts(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT email, refresh_token, history_id, last_sync_at
            FROM raw.gmail_account
            WHERE status = 'active'
              AND refresh_token IS NOT NULL
            ORDER BY last_sync_at NULLS FIRST
            """
        )


async def update_account_sync(
    pool: asyncpg.Pool, email: str, history_id: str | None
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE raw.gmail_account
               SET history_id = COALESCE($2, history_id),
                   last_sync_at = now()
             WHERE email = $1
            """,
            email, history_id,
        )


async def mark_reauth_needed(pool: asyncpg.Pool, email: str, reason: str) -> None:
    """Flag an account as needing re-consent. It then drops out of
    list_active_accounts (so we stop futile syncs) and the Sources page shows a
    'Reconnect needed' badge. The OAuth CLI resets status to 'active' on
    re-mint. `reason` is logged only (no column)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE raw.gmail_account SET status = 'reauth_needed' WHERE email = $1",
            email,
        )
    log.warning("account %s flagged reauth_needed: %s", email, reason)


async def insert_message(pool: asyncpg.Pool, row: dict[str, Any]) -> bool:
    """Returns True if inserted (new), False if skipped on conflict."""
    async with pool.acquire() as conn:
        result = await conn.fetchrow(
            """
            INSERT INTO raw.gmail_message
              (account_email, message_id, thread_id, rfc822_message_id,
               from_address, from_name, to_addresses, cc_addresses,
               bcc_addresses, subject, body_text, body_html,
               internal_date, labels, source, payload)
            VALUES
              ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16::jsonb)
            ON CONFLICT (account_email, message_id) DO NOTHING
            RETURNING id
            """,
            row["account_email"], row["message_id"], row.get("thread_id"),
            row.get("rfc822_message_id"), row.get("from_address"),
            row.get("from_name"), row.get("to_addresses") or [],
            row.get("cc_addresses") or [], row.get("bcc_addresses") or [],
            row.get("subject"), row.get("body_text"), row.get("body_html"),
            row["internal_date"], row.get("labels") or [],
            row.get("source", "live"),
            json.dumps(row.get("payload", {}), default=str),
        )
    return result is not None


async def upsert_events(pool: asyncpg.Pool, rows: list[dict[str, Any]]) -> int:
    """Upsert calendar events on (account_email, calendar_id, event_id).
    Returns the number of rows written (insert or update)."""
    if not rows:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO raw.gcal_event
              (account_email, calendar_id, event_id, summary, description,
               location, status, start_ts, end_ts, all_day, organizer_email,
               attendees, self_response, html_link, recurring_event_id,
               updated_ts, payload)
            VALUES
              ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13,$14,$15,$16,$17::jsonb)
            ON CONFLICT (account_email, calendar_id, event_id) DO UPDATE SET
              summary            = EXCLUDED.summary,
              description        = EXCLUDED.description,
              location           = EXCLUDED.location,
              status             = EXCLUDED.status,
              start_ts           = EXCLUDED.start_ts,
              end_ts             = EXCLUDED.end_ts,
              all_day            = EXCLUDED.all_day,
              organizer_email    = EXCLUDED.organizer_email,
              attendees          = EXCLUDED.attendees,
              self_response      = EXCLUDED.self_response,
              html_link          = EXCLUDED.html_link,
              recurring_event_id = EXCLUDED.recurring_event_id,
              updated_ts         = EXCLUDED.updated_ts,
              payload            = EXCLUDED.payload,
              ingested_at        = now()
            """,
            [(
                r["account_email"], r["calendar_id"], r["event_id"],
                r.get("summary"), r.get("description"), r.get("location"),
                r.get("status"), r.get("start_ts"), r.get("end_ts"),
                r.get("all_day", False), r.get("organizer_email"),
                json.dumps(r.get("attendees") or [], default=str),
                r.get("self_response"), r.get("html_link"),
                r.get("recurring_event_id"), r.get("updated_ts"),
                json.dumps(r.get("payload") or {}, default=str),
            ) for r in rows],
        )
    return len(rows)


async def cancel_missing_events(
    pool: asyncpg.Pool, account_email: str, calendar_id: str,
    window_start, window_end, keep_event_ids: list[str],
) -> int:
    """Reconcile the mirror against a full-window fetch: SOFT-CANCEL rows in
    [window_start, window_end) for this calendar that Google no longer returns
    (event deleted, cancelled, or moved out of the window). We fetch with
    showDeleted=False, so a deletion simply stops appearing — without this
    reconcile its row would live forever with status='confirmed' (the bug that
    kept deleted routines/events on the schedule).

    Soft-cancel (status='cancelled') rather than DELETE: it's auditable and
    recoverable, readers already hide cancelled, and if the event later
    reappears the upsert flips it back to 'confirmed'. Only rows not already
    cancelled are touched (so the count reflects real new cancellations).

    SAFETY: only call this after a SUCCESSFUL fetch. A fetch that errors returns
    early upstream, so this never runs with an empty keep-set due to failure —
    an empty keep-set here means the window is genuinely empty, and cancelling
    it is correct."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE raw.gcal_event
               SET status = 'cancelled'
             WHERE account_email = $1 AND calendar_id = $2
               AND start_ts >= $3 AND start_ts < $4
               AND coalesce(status, 'confirmed') <> 'cancelled'
               AND NOT (event_id = ANY($5::text[]))
            """,
            account_email, calendar_id, window_start, window_end, keep_event_ids,
        )
    # asyncpg returns e.g. "UPDATE 3"
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError, AttributeError):
        return 0


async def unread_message_ids(pool: asyncpg.Pool, account_email: str, *, days: int) -> list[str]:
    """Message ids we currently store as UNREAD within the reconcile window —
    the DB side of the read-state sync diff."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT message_id FROM raw.gmail_message
             WHERE account_email = $1
               AND 'UNREAD' = ANY(labels)
               AND internal_date > now() - ($2 || ' days')::interval
            """, account_email, str(days))
    return [r["message_id"] for r in rows]


async def mark_messages_read(pool: asyncpg.Pool, account_email: str, ids: list[str]) -> int:
    """Drop the UNREAD label from rows that Gmail no longer considers unread
    (read in another client). Returns rows changed."""
    if not ids:
        return 0
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE raw.gmail_message
               SET labels = array_remove(labels, 'UNREAD')
             WHERE account_email = $1 AND message_id = ANY($2)
               AND 'UNREAD' = ANY(labels)
            """, account_email, ids)
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0


async def mark_messages_unread(pool: asyncpg.Pool, account_email: str, ids: list[str]) -> int:
    """Restore the UNREAD label on rows Gmail says are unread but we don't
    (marked-unread in another client). Only touches rows we actually have."""
    if not ids:
        return 0
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE raw.gmail_message
               SET labels = labels || '{UNREAD}'
             WHERE account_email = $1 AND message_id = ANY($2)
               AND NOT ('UNREAD' = ANY(labels))
            """, account_email, ids)
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0


async def latest_message_date(
    pool: asyncpg.Pool, account_email: str
) -> str | None:
    """Returns 'YYYY/MM/DD' for the most recent message we have for this
    account, suitable for Gmail's q='after:...' search syntax. Returns
    None when the account is empty (do initial backfill)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT to_char(max(internal_date) AT TIME ZONE 'UTC', 'YYYY/MM/DD') AS d
            FROM raw.gmail_message
            WHERE account_email = $1
            """,
            account_email,
        )
    return row["d"] if row and row["d"] else None


async def earliest_message_date(
    pool: asyncpg.Pool, account_email: str
) -> str | None:
    """Mirror of latest_message_date for backfill (walk-backwards) mode."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT to_char(min(internal_date) AT TIME ZONE 'UTC', 'YYYY/MM/DD') AS d
            FROM raw.gmail_message
            WHERE account_email = $1
            """,
            account_email,
        )
    return row["d"] if row and row["d"] else None
