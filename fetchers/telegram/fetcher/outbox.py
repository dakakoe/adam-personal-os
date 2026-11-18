"""Outbound send poller — runs inside the live Telethon process (which holds
the session + the contact's cached entity). Picks up reviewed drafts the merge
API enqueued into memory.telegram_outbox and sends them as real Telegram
messages FROM the user's account. Nothing is enqueued without an explicit
"Send via Telegram" action in the UI.

Telegram answers the sending session with a short "sent" acknowledgement, not
a message update, so the live handler never sees what this poller sends.
Each send is therefore recorded here, from the Message the API returns —
the same row the handler would have written (ON CONFLICT keeps it single).
"""
from __future__ import annotations

import asyncio
import logging

import asyncpg
from telethon import TelegramClient

from . import raw

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 8
MAX_ATTEMPTS = 3


async def poll_loop(client: TelegramClient, pool: asyncpg.Pool, cfg=None) -> None:
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
                await _send_one(client, pool, r, cfg)
        except Exception:
            log.exception("outbox: poll iteration failed")
        await asyncio.sleep(POLL_INTERVAL_S)


async def resolve_recipient(client, pool: asyncpg.Pool, tg_user_id: int):
    """Something Telethon can send to. A bare id only works when this
    session has the user's access hash (seen them since login); after a
    session reset it does not, so fall back to the username we stored, then
    to warming the entity cache with the dialog list. Raises ValueError with
    a plain explanation when none works."""
    from telethon.tl.types import PeerUser
    try:
        return await client.get_input_entity(PeerUser(tg_user_id))
    except ValueError:
        pass
    username = await pool.fetchval(
        "SELECT username FROM raw.telegram_user WHERE source_user_id = $1", tg_user_id)
    if username:
        try:
            return await client.get_input_entity(username)
        except (ValueError, TypeError):
            pass
    await client.get_dialogs(limit=None)        # populates the session's entity cache
    try:
        return await client.get_input_entity(PeerUser(tg_user_id))
    except ValueError as e:
        raise ValueError(
            "Telegram does not let this session address that user yet: no shared dialog "
            "or username. Open the chat once in Telegram (or receive a message from them), "
            "then send again.") from e


_self_id: dict = {}


async def record_sent(pool: asyncpg.Pool, client, sent, cfg) -> bool:
    """Store the message we just sent as an ordinary raw row (text only; our
    own sends carry no voice). The Message Telegram returns to the sender
    has no from_id, so the row is stamped with our own id afterwards —
    otherwise the normalizer would read it as inbound. Best effort: a
    failure here must not mark the send itself as failed — it did go out."""
    if cfg is None or sent is None:
        return False
    try:
        inserted = bool(await raw.record_message(
            pool, client, sent, voice_dir=cfg.voice_dir, download_voice=False))
        if getattr(sent, "sender_id", None) is None:
            me = _self_id.get("id")
            if me is None:
                me = _self_id["id"] = (await client.get_me()).id
            await pool.execute(
                "UPDATE raw.telegram_message SET sender_id = $3 "
                "WHERE chat_id = $1 AND source_message_id = $2 AND sender_id IS NULL",
                sent.chat_id, sent.id, me)
        return inserted
    except Exception:  # noqa: BLE001
        log.warning("outbox: sent but could not record message %s", getattr(sent, "id", "?"), exc_info=True)
        return False


async def _send_one(client: TelegramClient, pool: asyncpg.Pool, r, cfg=None) -> None:
    oid = r["id"]
    try:
        target = await resolve_recipient(client, pool, int(r["tg_user_id"]))
        sent = await client.send_message(target, r["body"])
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

    await record_sent(pool, client, sent, cfg)
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
