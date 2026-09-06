"""DB access for the mail_ml trainer/classifier. Reads message text + weak-label
inputs from raw.gmail_message and upserts verdicts into memory.mail_class."""
from __future__ import annotations

import json
import os
from typing import Any

import asyncpg


def db_url() -> str:
    url = (os.environ.get("MAIL_ML_DATABASE_URL")
           or os.environ.get("MERGE_API_DATABASE_URL")
           or os.environ.get("DATABASE_URL"))
    if url:
        return url
    # Mirror the fetchers: build from POSTGRES_* (Postgres is published on the host
    # at 127.0.0.1:5432; the docker service name isn't reachable from a host worker).
    try:
        user = os.environ["POSTGRES_USER"]
        pw = os.environ["POSTGRES_PASSWORD"]
        db = os.environ["POSTGRES_DB"]
    except KeyError as e:
        raise SystemExit(f"missing DB env: set DATABASE_URL or POSTGRES_* ({e})")
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


def _headers(payload) -> dict:
    if not payload:
        return {}
    data = json.loads(payload) if isinstance(payload, str) else payload
    h = (data or {}).get("headers") or {}
    return h if isinstance(h, dict) else {}


async def connect() -> asyncpg.Pool:
    return await asyncpg.create_pool(db_url(), min_size=1, max_size=4, statement_cache_size=0)


async def fetch_training_rows(pool: asyncpg.Pool, *, limit: int) -> list[dict[str, Any]]:
    """A sample of messages with body text, for weak labeling + training. Newest
    first; skips empty-body rows (nothing for the model to read)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT subject, body_text, from_address, labels, payload
              FROM raw.gmail_message
             WHERE COALESCE(body_text, '') <> ''
             ORDER BY internal_date DESC
             LIMIT $1
            """, limit)
    return [{"subject": r["subject"], "body_text": r["body_text"],
             "from_address": r["from_address"],
             "labels": list(r["labels"] or []), "headers": _headers(r["payload"])}
            for r in rows]


async def fetch_unclassified(pool: asyncpg.Pool, *, limit: int) -> list[dict[str, Any]]:
    """Messages with no mail_class verdict yet (newest first), for batch inference."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.account_email, m.message_id, m.subject, m.body_text, m.from_address
              FROM raw.gmail_message m
             WHERE COALESCE(m.body_text, '') <> ''
               AND NOT EXISTS (SELECT 1 FROM memory.mail_class c
                                WHERE c.account_email = m.account_email
                                  AND c.message_id = m.message_id)
             ORDER BY m.internal_date DESC
             LIMIT $1
            """, limit)
    return [dict(r) for r in rows]


async def fetch_low_confidence(pool: asyncpg.Pool, *, threshold: float,
                               limit: int) -> list[dict[str, Any]]:
    """Classified messages the SetFit model was unsure about (confidence below
    threshold) and that the Ollama tier hasn't refined yet, oldest first. Joined
    back to the message text the LLM needs."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.account_email, c.message_id, c.confidence, c.model_version,
                   m.subject, m.body_text, m.from_address
              FROM memory.mail_class c
              JOIN raw.gmail_message m
                ON m.account_email = c.account_email AND m.message_id = c.message_id
             WHERE c.confidence < $1
               AND c.model_version NOT LIKE '%+ollama'
             ORDER BY c.classified_at ASC
             LIMIT $2
            """, threshold, limit)
    return [dict(r) for r in rows]


async def mark_refine_visited(pool: asyncpg.Pool, account_email: str,
                              message_id: str, version: str) -> None:
    """Stamp a row's model_version (keeping class/confidence) so the refine
    batch never re-picks it after an unusable Ollama reply. Never touches a
    user correction — those are ground truth."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE memory.mail_class SET model_version = $3 "
            "WHERE account_email = $1 AND message_id = $2 AND model_version <> 'user'",
            account_email, message_id, version)


async def upsert_classes(pool: asyncpg.Pool, verdicts: list[dict[str, Any]]) -> int:
    """verdicts: [{account_email, message_id, content_class, confidence, model_version}].
    A row the user corrected (model_version='user') is ground truth — the
    classifier/refiner must never overwrite it."""
    if not verdicts:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO memory.mail_class
              (account_email, message_id, content_class, confidence, model_version, classified_at)
            VALUES ($1, $2, $3, $4, $5, now())
            ON CONFLICT (account_email, message_id) DO UPDATE
              SET content_class = EXCLUDED.content_class, confidence = EXCLUDED.confidence,
                  model_version = EXCLUDED.model_version, classified_at = now()
            WHERE mail_class.model_version IS DISTINCT FROM 'user'
            """,
            [(v["account_email"], v["message_id"], v["content_class"],
              v.get("confidence"), v.get("model_version")) for v in verdicts])
    return len(verdicts)


async def fetch_user_labels(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """User-corrected classifications (model_version='user') joined to the
    message text — ground truth for the retrain eval metric."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.content_class, m.subject, m.body_text, m.from_address
              FROM memory.mail_class c
              JOIN raw.gmail_message m
                ON m.account_email = c.account_email AND m.message_id = c.message_id
             WHERE c.model_version = 'user'
               AND COALESCE(m.body_text, '') <> ''
            """)
    return [dict(r) for r in rows]
