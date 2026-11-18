from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

from .config import Config

log = logging.getLogger(__name__)

# raw.kind → canonical.channel
CHANNEL_BY_KIND: dict[str, str] = {
    "text": "telegram_text",
    "voice": "telegram_voice",
    "photo": "telegram_photo",
    "video": "telegram_video",
    "document": "telegram_document",
    "sticker": "telegram_sticker",
    "other": "telegram_other",
}


def _display_name(row: asyncpg.Record) -> str:
    parts = [p for p in (row["first_name"], row["last_name"]) if p]
    name = " ".join(parts).strip()
    if name:
        return name
    if row["username"]:
        return f"@{row['username']}"
    return f"Telegram user {row['source_user_id']}"


async def sync_persons(pool: asyncpg.Pool) -> tuple[int, int]:
    """Pass 1: ensure one canonical.person + canonical.identity per
    raw.telegram_user. Returns (rows_seen, persons_created)."""
    rows_seen = 0
    persons_created = 0

    async with pool.acquire() as conn:
        users = await conn.fetch(
            "SELECT source_user_id, username, first_name, last_name FROM raw.telegram_user"
        )

    for u in users:
        rows_seen += 1
        source_id = str(u["source_user_id"])
        display_name = _display_name(u)
        evidence = {
            "username": u["username"],
            "first_name": u["first_name"],
            "last_name": u["last_name"],
        }

        # Single-roundtrip idempotent upsert. If the identity exists we
        # return its person_id; otherwise create person+identity in one txn.
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    WITH existing AS (
                        SELECT person_id FROM canonical.identity
                        WHERE source = 'telegram' AND source_id = $1
                        LIMIT 1
                    ),
                    new_person AS (
                        INSERT INTO canonical.person (display_name)
                        SELECT $2
                        WHERE NOT EXISTS (SELECT 1 FROM existing)
                        RETURNING id
                    ),
                    new_identity AS (
                        INSERT INTO canonical.identity (person_id, source, source_id, evidence)
                        SELECT id, 'telegram', $1, $3::jsonb FROM new_person
                        RETURNING person_id
                    )
                    SELECT person_id, FALSE AS created FROM existing
                    UNION ALL
                    SELECT person_id, TRUE AS created FROM new_identity
                    """,
                    source_id,
                    display_name,
                    json.dumps(evidence, default=str),
                )
        if row and row["created"]:
            persons_created += 1

    return rows_seen, persons_created


async def _resolve_person_map(conn: asyncpg.Connection) -> dict[str, str]:
    """One-shot snapshot of canonical.identity → person_id (with merged_into
    collapsed for 1-hop merges). Returns {source_id_str: person_uuid_str}."""
    rows = await conn.fetch(
        """
        SELECT i.source_id,
               COALESCE(p.merged_into, p.id)::text AS person_id
        FROM canonical.identity i
        JOIN canonical.person p ON p.id = i.person_id
        WHERE i.source = 'telegram'
        """
    )
    return {r["source_id"]: r["person_id"] for r in rows}


async def sync_interactions(pool: asyncpg.Pool, cfg: Config) -> tuple[int, int]:
    """Pass 2: insert one canonical.interaction per unprocessed
    raw.telegram_message. Returns (rows_seen, rows_inserted)."""
    rows_seen = 0
    rows_inserted = 0

    self_id_str = str(cfg.self_user_id)

    async with pool.acquire() as conn:
        person_map = await _resolve_person_map(conn)
    log.info("person_map: %d telegram identities resolved", len(person_map))

    while True:
        async with pool.acquire() as conn:
            batch = await conn.fetch(
                """
                SELECT r.id, r.chat_id, r.sender_id, r.message_date,
                       r.kind, r.text
                FROM raw.telegram_message r
                LEFT JOIN canonical.interaction c
                  ON c.raw_source = 'raw.telegram_message' AND c.raw_id = r.id
                WHERE c.id IS NULL
                ORDER BY r.id
                LIMIT $1
                """,
                cfg.batch_size,
            )

        if not batch:
            break

        records: list[tuple[Any, ...]] = []
        for r in batch:
            rows_seen += 1
            chat_id = r["chat_id"]
            sender_id = r["sender_id"]
            kind = r["kind"]

            # Counterparty is always the chat_id in private chats. Note that
            # chat_id is the OTHER person's user_id in private dialogs, and is
            # populated for every message we ingested (filter=private).
            person_id = person_map.get(str(chat_id))

            if sender_id is None:
                direction = "inbound"  # channel posts; shouldn't happen given private filter
            elif sender_id == cfg.self_user_id:
                direction = "outbound"
            else:
                direction = "inbound"

            channel = CHANNEL_BY_KIND.get(kind, "telegram_other")
            body = r["text"]  # NULL for voice/photo/etc until enriched later

            records.append(
                (
                    person_id,
                    channel,
                    direction,
                    r["message_date"],
                    body,
                    "raw.telegram_message",
                    r["id"],
                )
            )

        async with pool.acquire() as conn:
            async with conn.transaction():
                # executemany via COPY is fastest but ON CONFLICT requires
                # plain INSERT; for 5000-row batches this is still very fast.
                result = await conn.executemany(
                    """
                    INSERT INTO canonical.interaction
                      (person_id, channel, direction, occurred_at, body, raw_source, raw_id)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (raw_source, raw_id) DO NOTHING
                    """,
                    records,
                )
        # executemany returns None on asyncpg, so count by len(batch).
        # Conflict-skipped rows are rare here (we LEFT JOIN-filtered them out
        # already) but possible under concurrent runs.
        rows_inserted += len(batch)

        log.info(
            "batch: %d rows; running totals seen=%d inserted=%d",
            len(batch),
            rows_seen,
            rows_inserted,
        )

    return rows_seen, rows_inserted


# Attribute group messages to whoever wrote them.
#
# person_id stays NULL on these rows on purpose — see the column comment on
# author_person_id. This only answers "who said it", which is what turns a
# retrieved group message from "(unknown)" into a name.
#
# Backlog-shaped: only rows still missing an author are touched, so the first
# run does the 42k backfill and every run after it is a cheap indexed no-op.
GROUP_AUTHOR_TELEGRAM_SQL = """
UPDATE canonical.interaction i
   SET author_person_id = COALESCE(p.merged_into, p.id)
  FROM raw.telegram_message m
  JOIN canonical.identity ci
    ON ci.source = 'telegram' AND ci.source_id = m.sender_id::text
  JOIN canonical.person p
    ON p.id = ci.person_id AND p.deleted_at IS NULL
 WHERE i.raw_source = 'raw.telegram_message'
   AND i.raw_id = m.id
   AND i.person_id IS NULL
   AND i.author_person_id IS NULL
   AND m.chat_id < 0
   -- Your own messages need no author: group_mention already records who you
   -- addressed, and naming yourself as the author of your own text is noise.
   AND m.sender_id <> $1
"""

GROUP_AUTHOR_WHATSAPP_SQL = """
UPDATE canonical.interaction i
   SET author_person_id = COALESCE(p.merged_into, p.id)
  FROM raw.whatsapp_message w
  JOIN canonical.identity ci
    ON ci.source = 'whatsapp'
   AND ci.source_id = CASE
         WHEN w.sender_jid LIKE '%@lid'
           THEN 'lid:' || split_part(w.sender_jid, '@', 1)
         ELSE split_part(w.sender_jid, '@', 1)
       END
  JOIN canonical.person p
    ON p.id = ci.person_id AND p.deleted_at IS NULL
 WHERE i.raw_source = 'raw.whatsapp_message'
   AND i.raw_id = w.id
   AND i.person_id IS NULL
   AND i.author_person_id IS NULL
   AND w.chat_jid LIKE '%@g.us'
   AND w.from_me = FALSE
   AND w.sender_jid IS NOT NULL
"""


async def sync_group_authors(pool: asyncpg.Pool, cfg: Config) -> tuple[int, int]:
    """Returns (telegram_rows, whatsapp_rows) newly attributed."""
    async def _run(sql: str, *args) -> int:
        async with pool.acquire() as conn:
            result = await conn.execute(sql, *args)
        try:
            return int(str(result).rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            return 0

    tg = await _run(GROUP_AUTHOR_TELEGRAM_SQL, cfg.self_user_id)
    wa = await _run(GROUP_AUTHOR_WHATSAPP_SQL)
    return tg, wa


async def run(cfg: Config) -> None:
    pool = await asyncpg.create_pool(cfg.db_url, min_size=1, max_size=4)
    try:
        log.info("normalizer: starting (self_id=%d batch=%d)", cfg.self_user_id, cfg.batch_size)
        seen, created = await sync_persons(pool)
        log.info("telegram persons: seen=%d created=%d", seen, created)
        seen, inserted = await sync_interactions(pool, cfg)
        log.info("telegram interactions: seen=%d inserted=%d", seen, inserted)
        mentions = await sync_group_mentions(pool, cfg)
        log.info("telegram group mentions: inserted=%d", mentions)
        tg_auth, wa_auth = await sync_group_authors(pool, cfg)
        log.info("group message authors: telegram=%d whatsapp=%d", tg_auth, wa_auth)

        # WhatsApp pass — no-ops cheaply when raw.whatsapp_* are empty.
        #
        # Runs after Telegram so that when the same human reaches us on both
        # channels, person creation order is deterministic. Order WITHIN the
        # block matters: LID identities must be promoted to their phone form
        # before persons are synced, or the same person is created twice.
        from . import whatsapp as whatsapp_normalizer
        upgraded, collisions = await whatsapp_normalizer.upgrade_lid_identities(pool)
        log.info("whatsapp lid upgrade: upgraded=%d collisions=%d", upgraded, collisions)
        seeded = await whatsapp_normalizer.seed_contacts_from_messages(pool)
        log.info("whatsapp contacts seeded from messages: %d", seeded)
        seen, created = await whatsapp_normalizer.sync_persons(pool)
        log.info("whatsapp persons: seen=%d created=%d", seen, created)
        signals = await whatsapp_normalizer.sync_phone_signals(pool)
        log.info("whatsapp phone signals: written=%d", signals)
        seen, inserted = await whatsapp_normalizer.sync_interactions(pool, cfg)
        log.info("whatsapp interactions: seen=%d inserted=%d", seen, inserted)
        repaired = await whatsapp_normalizer.attach_orphaned_interactions(pool)
        log.info("whatsapp orphaned interactions repaired: %d", repaired)
        mentions = await whatsapp_normalizer.sync_group_mentions(pool)
        log.info("whatsapp group mentions: inserted=%d", mentions)

        # Meetings pass — Granola recaps + calendar events -> people.
        # Runs AFTER gmail below would be wrong: attendee resolution needs the
        # email identities that the gmail pass creates, and those exist from
        # the previous cycle, so ordering here only affects how quickly a
        # brand-new contact's meeting attaches. Kept before gmail so a meeting
        # never waits on a full mail sync.
        from . import meetings as meetings_normalizer
        seen, res, unres = await meetings_normalizer.sync_participants(pool)
        log.info("meeting participants: seen=%d resolved=%d unresolved=%d", seen, res, unres)
        made = await meetings_normalizer.sync_interactions(pool, cfg)
        log.info("meeting interactions: inserted=%d", made)
        filled = await meetings_normalizer.backfill_bodies(pool)
        log.info("meeting bodies backfilled: %d", filled)

        # Gmail pass — no-ops cheaply when raw.gmail_message is empty.
        from . import gmail as gmail_normalizer
        seen, created = await gmail_normalizer.sync_persons_email(pool)
        log.info("email persons: seen=%d created=%d", seen, created)
        seen, inserted = await gmail_normalizer.sync_interactions_email(pool, cfg)
        log.info("email interactions: seen=%d inserted=%d", seen, inserted)

        # Google Contacts pass — no-op when raw.google_contact is empty.
        seen, created, upgraded = await gmail_normalizer.sync_persons_from_google_contacts(pool)
        log.info(
            "google contacts: seen=%d persons_created=%d names_upgraded=%d",
            seen, created, upgraded,
        )

        # Extract photo URLs from People API payloads → memory.person_photo
        # (link-only; bytes never leave Google). Idempotent.
        written = await gmail_normalizer.sync_photos_from_google_contacts(pool)
        log.info("google contacts photos: written=%d", written)

        # iPhone contacts pass — no-op unless an upload added or changed cards.
        # After WhatsApp and Google contacts, so their people exist to match.
        from . import iphone_contacts as iphone_normalizer
        stats = await iphone_normalizer.sync_contacts(pool)
        log.info("iphone contacts: %s", stats)

        # LinkedIn pass — no-ops cheaply when raw.linkedin_* tables are empty.
        from . import linkedin as linkedin_normalizer
        stats = await linkedin_normalizer.sync_connections(pool)
        log.info("linkedin connections: %s", stats)
        stats = await linkedin_normalizer.sync_messages(pool, cfg)
        log.info("linkedin messages: %s", stats)
        stats = await linkedin_normalizer.sync_imported_contacts(pool)
        log.info("linkedin imported_contacts: %s", stats)
    finally:
        await pool.close()

# Outbound group messages that @-mention someone we know.
#
# canonical.interaction can't hold these: it keys a counterparty off the CHAT,
# which for a group is nobody. An @mention is the one signal that says you
# addressed a specific person, so it's extracted here into memory.group_mention
# and used only to settle follow-ups and satisfy circle cadences.
#
# Handles resolve raw.telegram_user.username -> canonical.identity('telegram').
# There are no 'telegram_handle' identities in this install; the username on
# the user directory row is the only place a handle lives.
GROUP_MENTIONS_SQL = """
WITH mentioned AS (
  SELECT m.id AS raw_id,
         m.chat_id,
         m.message_date,
         lower((regexp_matches(m.text, '@([A-Za-z0-9_]{4,32})', 'g'))[1]) AS handle
    FROM raw.telegram_message m
   WHERE m.chat_id < 0                  -- negative chat_id = group/channel
     AND m.sender_id = $1               -- outbound: sent BY you
     AND m.text IS NOT NULL
     AND m.text LIKE '%@%'
)
-- raw_source is written explicitly rather than left to the column default,
-- and the conflict target must name it: memory.group_mention is now keyed
-- (person_id, raw_source, raw_id) because raw_id is an unFK'd pointer and
-- raw.telegram_message / raw.whatsapp_message have overlapping BIGSERIALs.
INSERT INTO memory.group_mention
  (person_id, raw_source, raw_id, chat_id, chat_key, handle, occurred_at)
SELECT i.person_id, 'raw.telegram_message', x.raw_id, x.chat_id,
       x.chat_id::text, x.handle, x.message_date
  FROM mentioned x
  JOIN raw.telegram_user u ON lower(u.username) = x.handle
  JOIN canonical.identity i
    ON i.source = 'telegram' AND i.source_id = u.source_user_id::text
  JOIN canonical.person p
    ON p.id = i.person_id AND p.merged_into IS NULL AND p.deleted_at IS NULL
ON CONFLICT (person_id, raw_source, raw_id) DO NOTHING
"""


async def sync_group_mentions(pool, cfg) -> int:
    """Idempotent full pass. ON CONFLICT makes re-runs free, and the scan is
    bounded by the group messages we actually hold, so there's no cursor to
    keep and nothing to go stale if a handle is learned late."""
    async with pool.acquire() as conn:
        result = await conn.execute(GROUP_MENTIONS_SQL, cfg.self_user_id)
    # "INSERT 0 <n>"
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0
