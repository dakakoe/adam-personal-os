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


async def run(cfg: Config) -> None:
    pool = await asyncpg.create_pool(cfg.db_url, min_size=1, max_size=4)
    try:
        log.info("normalizer: starting (self_id=%d batch=%d)", cfg.self_user_id, cfg.batch_size)
        seen, created = await sync_persons(pool)
        log.info("telegram persons: seen=%d created=%d", seen, created)
        seen, inserted = await sync_interactions(pool, cfg)
        log.info("telegram interactions: seen=%d inserted=%d", seen, inserted)

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
