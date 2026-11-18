from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

log = logging.getLogger(__name__)


async def connect(db_url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=4,
        statement_cache_size=0,  # safer when pgvector/pgcrypto types appear later
    )


async def mark_source_status(
    pool: asyncpg.Pool, source_key: str, status: str, reason: str | None = None,
) -> None:
    """Flag a data source's health for the Sources page (memory.source_status).
    Best-effort: a status-write failure must NEVER take down ingest, so all
    errors are swallowed (e.g. the table not existing yet before its migration
    is applied)."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memory.source_status (source_key, status, reason, updated_at)
                VALUES ($1, $2, $3, now())
                ON CONFLICT (source_key) DO UPDATE
                   SET status = EXCLUDED.status,
                       reason = EXCLUDED.reason,
                       updated_at = now()
                """,
                source_key, status, reason,
            )
    except Exception:
        log.warning("source_status upsert failed for %s (non-fatal)", source_key,
                    exc_info=True)


async def upsert_user(
    pool: asyncpg.Pool,
    *,
    source_user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    phone: str | None,
    is_bot: bool,
    payload: dict[str, Any],
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO raw.telegram_user
              (source_user_id, username, first_name, last_name, phone, is_bot, payload)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)
            ON CONFLICT (source_user_id) DO UPDATE SET
              username   = EXCLUDED.username,
              first_name = EXCLUDED.first_name,
              last_name  = EXCLUDED.last_name,
              phone      = COALESCE(EXCLUDED.phone, raw.telegram_user.phone),
              is_bot     = EXCLUDED.is_bot,
              last_seen  = now(),
              payload    = EXCLUDED.payload
            """,
            source_user_id,
            username,
            first_name,
            last_name,
            phone,
            is_bot,
            json.dumps(payload, default=str),
        )


async def upsert_chat(
    pool: asyncpg.Pool,
    *,
    source_chat_id: int,
    kind: str,
    title: str | None,
    username: str | None,
    payload: dict[str, Any],
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO raw.telegram_chat
              (source_chat_id, kind, title, username, payload)
            VALUES ($1,$2,$3,$4,$5::jsonb)
            ON CONFLICT (source_chat_id) DO UPDATE SET
              kind      = EXCLUDED.kind,
              title     = EXCLUDED.title,
              username  = EXCLUDED.username,
              last_seen = now(),
              payload   = EXCLUDED.payload
            """,
            source_chat_id,
            kind,
            title,
            username,
            json.dumps(payload, default=str),
        )


async def insert_message(
    pool: asyncpg.Pool,
    *,
    chat_id: int,
    source_message_id: int,
    sender_id: int | None,
    message_date,
    kind: str,
    text: str | None,
    voice_file_path: str | None,
    payload: dict[str, Any],
) -> bool:
    """Returns True if a new row was inserted, False if it was a duplicate."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO raw.telegram_message
              (chat_id, source_message_id, sender_id, message_date,
               kind, text, voice_file_path, payload)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
            ON CONFLICT (chat_id, source_message_id) DO NOTHING
            RETURNING id
            """,
            chat_id,
            source_message_id,
            sender_id,
            message_date,
            kind,
            text,
            voice_file_path,
            json.dumps(payload, default=str),
        )
        return row is not None
