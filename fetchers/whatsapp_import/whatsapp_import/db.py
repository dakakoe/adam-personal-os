"""Writing the extracted archive into raw.whatsapp_*.

Everything lands in the SAME tables the live bridge writes, so the normalizer
cannot tell an imported message from a live one and needs no special case.

Loading is COPY-into-a-temp-table then one INSERT..SELECT, rather than a
statement per row: an archive is often six figures of messages, and over an
SSH tunnel a round trip each would take hours.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Iterable

import asyncpg

log = logging.getLogger(__name__)

BATCH = 5000


async def connect(db_url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(db_url, min_size=1, max_size=2, statement_cache_size=0)


async def load_contacts(pool: asyncpg.Pool, rows: list[dict[str, Any]]) -> int:
    """Fill in names, never overwrite them.

    The address-book name from the phone is the best name we will ever have,
    but a contact may already exist from the live bridge, and COALESCE means a
    NULL here can never erase what is already known.
    """
    if not rows:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO raw.whatsapp_contact
              (jid, phone_e164, lid, notify_name, push_name)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (jid) DO UPDATE SET
              phone_e164  = COALESCE(raw.whatsapp_contact.phone_e164, EXCLUDED.phone_e164),
              lid         = COALESCE(raw.whatsapp_contact.lid, EXCLUDED.lid),
              notify_name = COALESCE(raw.whatsapp_contact.notify_name, EXCLUDED.notify_name),
              push_name   = COALESCE(raw.whatsapp_contact.push_name, EXCLUDED.push_name),
              last_seen   = now()
            """,
            [
                (r["jid"], r.get("phone_e164"), r.get("lid"),
                 r.get("notify_name"), r.get("push_name"))
                for r in rows
            ],
        )
    return len(rows)


async def load_groups(pool: asyncpg.Pool, rows: list[dict[str, Any]]) -> int:
    """Record groups WITHOUT enabling them.

    Deliberate: importing history must never silently switch on ingestion for
    a group you have not opted into. It only makes the list you choose from
    complete, and gives it real titles.
    """
    if not rows:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO raw.whatsapp_group_allowlist (chat_jid, title)
            VALUES ($1, $2)
            ON CONFLICT (chat_jid) DO UPDATE SET
              title        = COALESCE(raw.whatsapp_group_allowlist.title, EXCLUDED.title),
              last_seen_at = now()
            """,
            [(r["chat_jid"], r.get("title")) for r in rows],
        )
    return len(rows)


async def enabled_groups(pool: asyncpg.Pool) -> set[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_jid FROM raw.whatsapp_group_allowlist WHERE enabled = TRUE"
        )
    return {r["chat_jid"] for r in rows}


_STAGE_DDL = """
CREATE TEMP TABLE _wa_stage (
  chat_jid           TEXT,
  chat_key           TEXT,
  source_message_id  TEXT,
  from_me            BOOLEAN,
  sender_jid         TEXT,
  sender_phone_e164  TEXT,
  message_date       TIMESTAMPTZ,
  kind               TEXT,
  text               TEXT,
  payload            JSONB
) ON COMMIT DROP
"""

# Fill-nulls-only. A live row already carries a downloaded voice file and a
# richer payload; the archive must add what is missing without clobbering it.
_MERGE = """
INSERT INTO raw.whatsapp_message
  (chat_jid, chat_key, source_message_id, from_me, sender_jid,
   sender_phone_e164, message_date, origin, kind, text, payload)
SELECT chat_jid, chat_key, source_message_id, from_me, sender_jid,
       sender_phone_e164, message_date, $1, kind, text, payload
  FROM _wa_stage
ON CONFLICT (source_message_id, from_me) DO UPDATE SET
  text              = COALESCE(raw.whatsapp_message.text, EXCLUDED.text),
  sender_jid        = COALESCE(raw.whatsapp_message.sender_jid, EXCLUDED.sender_jid),
  sender_phone_e164 = COALESCE(raw.whatsapp_message.sender_phone_e164, EXCLUDED.sender_phone_e164),
  chat_key          = COALESCE(NULLIF(raw.whatsapp_message.chat_key, ''), EXCLUDED.chat_key)
"""


async def load_messages(
    pool: asyncpg.Pool, rows: Iterable[dict[str, Any]], *, origin: str = "iphone_backup"
) -> tuple[int, int]:
    """Returns (staged, present_after) — the second read back from the table,
    because ON CONFLICT DO UPDATE cannot distinguish an insert from an update
    in its row count, and a claimed insert count that is really an update
    count would make the idempotency check meaningless."""
    staged = 0
    batch: list[tuple] = []

    async with pool.acquire() as conn:
        before = await conn.fetchval("SELECT count(*) FROM raw.whatsapp_message")

        async def flush(records: list[tuple]) -> None:
            if not records:
                return
            async with conn.transaction():
                await conn.execute(_STAGE_DDL)
                await conn.copy_records_to_table("_wa_stage", records=records)
                await conn.execute(_MERGE, origin)

        for r in rows:
            batch.append((
                r["chat_jid"], r["chat_key"], r["source_message_id"], r["from_me"],
                r.get("sender_jid"), r.get("sender_phone_e164"),
                _as_dt(r["message_date"]), r["kind"], r.get("text"),
                json.dumps(r.get("payload") or {}, default=str),
            ))
            staged += 1
            if len(batch) >= BATCH:
                await flush(batch)
                batch = []
                log.info("staged %d messages", staged)
        await flush(batch)

        after = await conn.fetchval("SELECT count(*) FROM raw.whatsapp_message")

    return staged, after - before


def _as_dt(v: Any) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


async def live_window_start(pool: asyncpg.Pool) -> dict[str, Any]:
    """Earliest live-captured message per chat.

    A chat export has no real message ids, so an exported message and the same
    message captured live get different ids and would both be stored — the
    conversation would read twice. Since live capture began at pairing and an
    export is history, the clean rule is: refuse export rows at or after the
    moment live capture started for that chat.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chat_key, min(message_date) AS first_live
              FROM raw.whatsapp_message
             WHERE origin = 'live'
             GROUP BY chat_key
            """
        )
    return {r["chat_key"]: r["first_live"] for r in rows}


async def load_export_messages(
    pool: asyncpg.Pool, rows: list[dict[str, Any]]
) -> tuple[int, int, int]:
    """Load export-derived rows, dropping any that overlap live capture.

    Returns (staged, skipped_overlap, rows_actually_new).
    """
    cutoffs = await live_window_start(pool)
    keep, skipped = [], 0
    for r in rows:
        cutoff = cutoffs.get(r["chat_key"])
        if cutoff is not None and _as_dt(r["message_date"]) >= cutoff:
            skipped += 1
            continue
        keep.append(r)
    staged, new = await load_messages(pool, keep, origin="chat_export")
    return staged, skipped, new


async def suggest_numbers(pool: asyncpg.Pool, names: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Guess whose chat each export is, by matching its name to people we know.

    An export gives a display name and nothing else, so the number has to come
    from somewhere. Telegram, LinkedIn and Google Contacts have already put
    phone numbers on many of these people, so most rows can be pre-filled and
    the user only fixes the rest.

    Returns candidates per name — never picks one. Attaching a conversation to
    the wrong person is far worse than leaving a blank to fill in.
    """
    if not names:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT
                   p.display_name,
                   s.value AS phone,
                   p.id::text AS person_id
              FROM canonical.person p
              JOIN memory.extracted_signal s
                ON s.person_id = p.id AND s.signal_type = 'phone'
             WHERE p.deleted_at IS NULL AND p.merged_into IS NULL
               AND lower(p.display_name) = ANY($1::text[])
            UNION
            SELECT DISTINCT
                   p.display_name,
                   u.phone,
                   p.id::text
              FROM canonical.person p
              JOIN canonical.identity i
                ON i.person_id = p.id AND i.source = 'telegram'
              JOIN raw.telegram_user u
                ON u.source_user_id::text = i.source_id AND u.phone IS NOT NULL
             WHERE p.deleted_at IS NULL AND p.merged_into IS NULL
               AND lower(p.display_name) = ANY($1::text[])
            """,
            [n.lower() for n in names],
        )
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        key = r["display_name"].lower()
        phone = "".join(ch for ch in (r["phone"] or "") if ch.isdigit())
        if not phone:
            continue
        out.setdefault(key, []).append(
            {"display_name": r["display_name"], "phone": phone, "person_id": r["person_id"]}
        )
    return out
