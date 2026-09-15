"""Outbound send poller — runs inside the live Telethon process (which holds
the session + the contact's cached entity). Picks up reviewed drafts the merge
API enqueued into memory.telegram_outbox and sends them as real Telegram
messages FROM the user's account. Nothing is enqueued without an explicit
"Send via Telegram" action in the UI.

The sent message is also seen by the live NewMessage(outgoing=True) handler, so
it's recorded as a normal interaction — no special logging here.
"""
from __future__ import annotations

import asyncio
import logging

import asyncpg
from telethon import TelegramClient

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 8
MAX_ATTEMPTS = 3


async def poll_loop(client: TelegramClient, pool: asyncpg.Pool) -> None:
    log.info("outbox: poller started (every %ds)", POLL_INTERVAL_S)
    while True:
        try:
            rows = await pool.fetch(
                """
                SELECT id::text, person_id::text, draft_id::text, tg_user_id, body, attempts
                  FROM memory.telegram_outbox
                 WHERE status = 'pending'
                 ORDER BY created_at
                 LIMIT 5
                """
            )
            for r in rows:
                await _send_one(client, pool, r)
        except Exception:
            log.exception("outbox: poll iteration failed")
        await asyncio.sleep(POLL_INTERVAL_S)


async def _send_one(client: TelegramClient, pool: asyncpg.Pool, r) -> None:
    oid = r["id"]
    try:
        await client.send_message(int(r["tg_user_id"]), r["body"])
    except Exception as e:  # noqa: BLE001 — surface to the outbox row
        log.exception("outbox: send failed for %s", oid)
        await pool.execute(
            """
            UPDATE memory.telegram_outbox
               SET attempts = attempts + 1,
                   error = $2,
                   status = CASE WHEN attempts + 1 >= $3 THEN 'failed' ELSE 'pending' END
             WHERE id = $1::uuid
            """,
            oid, str(e)[:300], MAX_ATTEMPTS,
        )
        return

    await pool.execute(
        "UPDATE memory.telegram_outbox SET status='sent', sent_at=now(), attempts=attempts+1 WHERE id=$1::uuid",
        oid,
    )
    if r["draft_id"]:
        await pool.execute(
            "UPDATE memory.draft SET status='sent', decided_at=now() WHERE id=$1::uuid AND status='draft'",
            r["draft_id"],
        )
    log.info("outbox: sent %s to tg_user=%s", oid, r["tg_user_id"])
