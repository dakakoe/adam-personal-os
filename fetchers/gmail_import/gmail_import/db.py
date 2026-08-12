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


async def ensure_account(pool: asyncpg.Pool, email: str) -> None:
    """Idempotent INSERT — used by import to record that we've ingested
    something for this address even if no live OAuth is set up yet."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO raw.gmail_account (email, status)
            VALUES ($1, 'import-only')
            ON CONFLICT (email) DO NOTHING
            """,
            email,
        )


async def insert_batch(pool: asyncpg.Pool, rows: list[dict[str, Any]]) -> int:
    """Bulk insert with executemany. ON CONFLICT DO NOTHING handles
    re-running on overlapping exports. Returns the count actually inserted
    by diff'ing the affected-row count.

    NOTE: asyncpg's executemany doesn't return per-statement row counts in
    one round-trip, so we use copy_records_to_table is unfortunately blocked
    by the ON CONFLICT requirement; sticking with executemany and reporting
    `attempted` instead of `inserted`.
    """
    if not rows:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO raw.gmail_message
              (account_email, message_id, thread_id, rfc822_message_id,
               from_address, from_name, to_addresses, cc_addresses,
               bcc_addresses, subject, body_text, body_html,
               internal_date, labels, source, payload)
            VALUES
              ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16::jsonb)
            ON CONFLICT (account_email, message_id) DO NOTHING
            """,
            [
                (
                    r["account_email"], r["message_id"], r["thread_id"],
                    r["rfc822_message_id"], r["from_address"], r["from_name"],
                    r["to_addresses"], r["cc_addresses"], r["bcc_addresses"],
                    r["subject"], r["body_text"], r["body_html"],
                    r["internal_date"], r["labels"], r["source"],
                    json.dumps({"labels": r["labels"]}, default=str),
                )
                for r in rows
            ],
        )
    return len(rows)
