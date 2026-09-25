from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

from .config import Config

log = logging.getLogger(__name__)

# raw.kind → canonical.interaction.channel.
#
# WhatsApp's own vocabulary rather than Telegram parity ('image', not 'photo').
# These strings are baked into rows the moment they're written, and the only
# place they reach a human is channelLabel() in the UI, which matches on the
# 'whatsapp' prefix rather than the suffix.
CHANNEL_BY_KIND: dict[str, str] = {
    "text": "whatsapp_text",
    "voice": "whatsapp_voice",
    "audio": "whatsapp_audio",
    "image": "whatsapp_image",
    "video": "whatsapp_video",
    "gif": "whatsapp_gif",
    "document": "whatsapp_document",
    "sticker": "whatsapp_sticker",
    "location": "whatsapp_location",
    "contact_card": "whatsapp_contact_card",
    "poll": "whatsapp_poll",
    "other": "whatsapp_other",
}

RAW_SOURCE = "raw.whatsapp_message"
IDENTITY_SOURCE = "whatsapp"


def chat_key_to_source_id(chat_key: str | None) -> str | None:
    """canonical.identity.source_id for a 1:1 chat.

    Bare international digits, or 'lid:<n>' when WhatsApp has only ever shown
    us a hidden id. Deliberately NOT the raw jid: the iPhone importer only
    ever sees phone jids while the live socket may only have a LID, so keying
    on the jid would split one human into two identities that the
    never-auto-merge rule would then keep apart forever. Digits are the one
    representation both writers can reach — and they're the same shape
    enrichment's _norm_phone() produces, so WhatsApp numbers compare directly
    against Telegram's.

    Group chats return None: a group is not a person.
    """
    if not chat_key:
        return None
    if chat_key.endswith("@g.us"):
        return None
    if chat_key.startswith("lid:"):
        return chat_key
    return chat_key if chat_key.isdigit() else None


def display_name_for(row: asyncpg.Record) -> str:
    """Best available name at the moment the person is created.

    notify_name is the address-book name off the user's own phone and is the
    only high-trust option; push_name is whatever the contact chose to call
    themselves, which for anyone who isn't a friend is often a shop name. We
    still fall back to it, because a bad name beats a bare number for
    recognising who a row is about.
    """
    for field in ("notify_name", "push_name", "business_name"):
        val = (row[field] or "").strip() if field in row.keys() else ""
        if val:
            return val
    phone = row["phone_e164"]
    if phone:
        return f"+{phone}"
    return f"WhatsApp {row['jid'].split('@')[0]}"


async def upgrade_lid_identities(pool: asyncpg.Pool) -> tuple[int, int]:
    """Pass 0: turn 'lid:<n>' identities into their phone-digit form once a
    mapping has been learned.

    Someone first seen behind a hidden id gets a 'lid:N' identity. When the
    bridge later observes that LID alongside a phone jid, this promotes the
    identity in place, so the person keeps their history rather than a second
    person appearing beside them.

    If BOTH forms already exist as identities they are two person rows for one
    human, and UNIQUE(source, source_id) means we cannot simply rewrite one
    into the other. We do not merge — that's a review-queue decision, per the
    project's never-auto-match-at-ingest rule — so those are counted and
    reported, and the enrichment candidate generator proposes the merge.

    Returns (upgraded, collisions_left_alone).
    """
    async with pool.acquire() as conn:
        pairs = await conn.fetch(
            """
            SELECT lid, phone_jid FROM raw.whatsapp_lid_map
            """
        )

    upgraded = 0
    collisions = 0
    for p in pairs:
        lid_local = str(p["lid"]).split("@")[0]
        phone_local = str(p["phone_jid"]).split("@")[0]
        if not phone_local.isdigit():
            continue
        lid_source_id = f"lid:{lid_local}"

        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    WITH lid_row AS (
                        SELECT id FROM canonical.identity
                         WHERE source = $1 AND source_id = $2
                    ),
                    phone_row AS (
                        SELECT id FROM canonical.identity
                         WHERE source = $1 AND source_id = $3
                    ),
                    promoted AS (
                        UPDATE canonical.identity
                           SET source_id = $3
                         WHERE id IN (SELECT id FROM lid_row)
                           AND NOT EXISTS (SELECT 1 FROM phone_row)
                        RETURNING id
                    )
                    SELECT
                      (SELECT count(*) FROM promoted)   AS promoted,
                      (SELECT count(*) FROM lid_row)    AS had_lid,
                      (SELECT count(*) FROM phone_row)  AS had_phone
                    """,
                    IDENTITY_SOURCE,
                    lid_source_id,
                    phone_local,
                )
        if not row:
            continue
        if row["promoted"]:
            upgraded += 1
        elif row["had_lid"] and row["had_phone"]:
            collisions += 1

    return upgraded, collisions


async def seed_contacts_from_messages(pool: asyncpg.Pool) -> int:
    """Ensure every 1:1 chat we hold messages for has a contact row.

    sync_persons() reads the contact directory, so a chat that produced
    messages but never a contact yields no person, and its interactions attach
    to nobody. That is not hypothetical: it is exactly what happens for a
    conversation you started and the other side has not replied to, and the
    iPhone importer hits it at scale because it writes messages without a
    contact directory at all.

    Deliberately writes no name — only the jid and phone. A name invented here
    would outrank the real one later, since display names are set once at
    person creation and never revised.

    Groups are skipped: a group is not a person.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO raw.whatsapp_contact (jid, phone_e164, lid)
            SELECT DISTINCT
                   m.chat_jid,
                   CASE WHEN m.chat_jid LIKE '%@s.whatsapp.net'
                          AND split_part(m.chat_jid, '@', 1) ~ '^[0-9]+$'
                        THEN split_part(m.chat_jid, '@', 1) END,
                   CASE WHEN m.chat_jid LIKE '%@lid' THEN m.chat_jid END
              FROM raw.whatsapp_message m
             WHERE m.chat_jid NOT LIKE '%@g.us'
            ON CONFLICT (jid) DO NOTHING
            """
        )
    try:
        return int(str(result).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def attach_orphaned_interactions(pool: asyncpg.Pool) -> int:
    """Give a person to WhatsApp interactions that were inserted before one
    existed.

    person_id is resolved once, at insert time, and the backlog query is an
    anti-join — so a message normalized before its contact was known stays
    orphaned forever with no error. This repairs those in place. Idempotent,
    and cheap because it only ever touches rows still lacking a person.

    Group interactions are left alone: their person_id is NULL by design, not
    by accident, which is what memory.group_mention exists to compensate for.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE canonical.interaction i
               SET person_id = resolved.person_id
              FROM (
                SELECT m.id AS raw_id, COALESCE(p.merged_into, p.id) AS person_id
                  FROM raw.whatsapp_message m
                  -- chat_key is already the identity's source_id form (bare
                  -- digits, or 'lid:N'). The second arm covers a chat whose
                  -- identity has since been upgraded off its LID: the message
                  -- still carries the old key, so resolve it through the map.
                  LEFT JOIN raw.whatsapp_lid_map l
                    ON 'lid:' || split_part(l.lid, '@', 1) = m.chat_key
                  JOIN canonical.identity ci
                    ON ci.source = $2
                   AND ci.source_id IN (
                         m.chat_key,
                         split_part(l.phone_jid, '@', 1)
                       )
                  JOIN canonical.person p ON p.id = ci.person_id
                 WHERE m.chat_jid NOT LIKE '%@g.us'
                   AND p.deleted_at IS NULL
              ) resolved
             WHERE i.raw_source = $1
               AND i.raw_id = resolved.raw_id
               AND i.person_id IS NULL
            """,
            RAW_SOURCE,
            IDENTITY_SOURCE,
        )
    try:
        return int(str(result).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def sync_persons(pool: asyncpg.Pool) -> tuple[int, int]:
    """Pass 1: one canonical.person + canonical.identity per WhatsApp contact.

    Same single-round-trip get-or-create as the Telegram pass. Names are only
    ever set at creation — an existing person is never renamed, because
    push_name is self-declared and would otherwise let a contact rewrite how
    they appear in your CRM.

    Returns (rows_seen, persons_created).
    """
    rows_seen = 0
    persons_created = 0

    async with pool.acquire() as conn:
        contacts = await conn.fetch(
            """
            SELECT jid, phone_e164, lid, push_name, notify_name, business_name
              FROM raw.whatsapp_contact
             WHERE is_me = false
            """
        )

    for c in contacts:
        # Reuse the chat-key rules so a contact and their 1:1 chat always
        # resolve to the same identity.
        local = c["jid"].split("@")[0]
        if c["jid"].endswith("@lid"):
            source_id = f"lid:{local}"
        elif c["phone_e164"]:
            source_id = c["phone_e164"]
        elif local.isdigit():
            source_id = local
        else:
            continue

        rows_seen += 1
        evidence = {
            "jid": c["jid"],
            "push_name": c["push_name"],
            "notify_name": c["notify_name"],
            "business_name": c["business_name"],
        }

        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    WITH existing AS (
                        SELECT person_id FROM canonical.identity
                         WHERE source = $4 AND source_id = $1
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
                        SELECT id, $4, $1, $3::jsonb FROM new_person
                        RETURNING person_id
                    )
                    SELECT person_id, FALSE AS created FROM existing
                    UNION ALL
                    SELECT person_id, TRUE AS created FROM new_identity
                    """,
                    source_id,
                    display_name_for(c),
                    json.dumps(evidence, default=str),
                    IDENTITY_SOURCE,
                )
        if row and row["created"]:
            persons_created += 1

    return rows_seen, persons_created


async def sync_phone_signals(pool: asyncpg.Pool) -> int:
    """Record each WhatsApp person's number as an extracted signal.

    Deliberately NOT a canonical.identity(source='phone') row. That table's
    UNIQUE(source, source_id) is global, so the first person to claim a number
    wins and every later one is silently swallowed or, worse, silently
    attached to the wrong person. memory.extracted_signal is unique
    per-person, so it can hold the same number for two people without lying
    about it — and it is exactly what the merge-candidate generator reads.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO memory.extracted_signal
              (person_id, signal_type, value, confidence, source)
            SELECT COALESCE(p.merged_into, p.id), 'phone', i.source_id, 'high', $1
              FROM canonical.identity i
              JOIN canonical.person p ON p.id = i.person_id
             WHERE i.source = $1
               AND i.source_id ~ '^[0-9]+$'
               AND p.deleted_at IS NULL
            ON CONFLICT (person_id, signal_type, value, source) DO NOTHING
            """,
            IDENTITY_SOURCE,
        )
    try:
        return int(str(result).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def _resolve_person_map(conn: asyncpg.Connection) -> dict[str, str]:
    """Snapshot of whatsapp source_id → person_id, one merge hop collapsed."""
    rows = await conn.fetch(
        """
        SELECT i.source_id,
               COALESCE(p.merged_into, p.id)::text AS person_id
          FROM canonical.identity i
          JOIN canonical.person p ON p.id = i.person_id
         WHERE i.source = $1
        """,
        IDENTITY_SOURCE,
    )
    return {r["source_id"]: r["person_id"] for r in rows}


async def sync_interactions(pool: asyncpg.Pool, cfg: Config) -> tuple[int, int]:
    """Pass 2: one canonical.interaction per unprocessed raw.whatsapp_message.

    Same anti-join backlog shape as the Telegram pass. Direction is read
    straight off the from_me column rather than compared against a configured
    self-id — strictly better, and it means the normalizer needs no new
    required env var (config.load() uses _required(), so a missing one would
    hard-fail every other pass too).

    Returns (rows_seen, rows_inserted).
    """
    rows_seen = 0
    rows_inserted = 0

    async with pool.acquire() as conn:
        person_map = await _resolve_person_map(conn)
    log.info("person_map: %d whatsapp identities resolved", len(person_map))

    while True:
        async with pool.acquire() as conn:
            batch = await conn.fetch(
                """
                SELECT r.id, r.chat_key, r.from_me, r.message_date, r.kind, r.text
                  FROM raw.whatsapp_message r
                  LEFT JOIN canonical.interaction c
                    ON c.raw_source = $1 AND c.raw_id = r.id
                 WHERE c.id IS NULL
                 ORDER BY r.id
                 LIMIT $2
                """,
                RAW_SOURCE,
                cfg.batch_size,
            )

        if not batch:
            break

        records: list[tuple[Any, ...]] = []
        for r in batch:
            rows_seen += 1
            # A group message has no single counterparty, so person_id stays
            # NULL — same as Telegram, and the reason memory.group_mention
            # exists to catch the one case where you did address someone.
            source_id = chat_key_to_source_id(r["chat_key"])
            person_id = person_map.get(source_id) if source_id else None

            records.append(
                (
                    person_id,
                    CHANNEL_BY_KIND.get(r["kind"], "whatsapp_other"),
                    "outbound" if r["from_me"] else "inbound",
                    r["message_date"],
                    r["text"],  # NULL for voice until Whisper fills it in
                    RAW_SOURCE,
                    r["id"],
                )
            )

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(
                    """
                    INSERT INTO canonical.interaction
                      (person_id, channel, direction, occurred_at, body, raw_source, raw_id)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (raw_source, raw_id) DO NOTHING
                    """,
                    records,
                )
        # asyncpg's executemany returns None, so this counts attempts. The
        # LEFT JOIN already filtered duplicates out, so the two only diverge
        # under a concurrent run.
        rows_inserted += len(batch)

        log.info(
            "batch: %d rows; running totals seen=%d inserted=%d",
            len(batch), rows_seen, rows_inserted,
        )

    return rows_seen, rows_inserted


# Outbound group messages that mention someone we know.
#
# Same premise as the Telegram extractor: canonical.interaction keys its
# counterparty off the chat, which in a group is nobody, so "I asked her about
# it in the founders group" would otherwise be invisible and she'd keep showing
# as months overdue.
#
# Simpler than Telegram's, though: WhatsApp delivers mentions structured in
# mentioned_jids, so there is no regex, and therefore no chance of matching a
# handle inside a URL or an email address.
GROUP_MENTIONS_SQL = """
INSERT INTO memory.group_mention
  (person_id, raw_source, raw_id, chat_key, handle, occurred_at)
SELECT i.person_id, $1, m.id, m.chat_key, mention.source_id, m.message_date
  FROM raw.whatsapp_message m
  CROSS JOIN LATERAL unnest(m.mentioned_jids) AS mj(jid)
  CROSS JOIN LATERAL (
    SELECT CASE
             WHEN mj.jid LIKE '%@lid' THEN 'lid:' || split_part(mj.jid, '@', 1)
             ELSE split_part(mj.jid, '@', 1)
           END AS source_id
  ) AS mention
  JOIN canonical.identity i
    ON i.source = $2 AND i.source_id = mention.source_id
  JOIN canonical.person p
    ON p.id = i.person_id AND p.merged_into IS NULL AND p.deleted_at IS NULL
 WHERE m.from_me = TRUE
   AND m.chat_jid LIKE '%@g.us'
   AND m.mentioned_jids IS NOT NULL
ON CONFLICT (person_id, raw_source, raw_id) DO NOTHING
"""


async def sync_group_mentions(pool: asyncpg.Pool) -> int:
    """Full idempotent re-scan, matching the Telegram extractor's shape."""
    async with pool.acquire() as conn:
        result = await conn.execute(GROUP_MENTIONS_SQL, RAW_SOURCE, IDENTITY_SOURCE)
    try:
        return int(str(result).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0
