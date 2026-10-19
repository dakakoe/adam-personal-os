from __future__ import annotations

import asyncio
import logging
import os
import time

import asyncpg
from telethon import TelegramClient, events
from telethon.tl import types
from telethon.tl.functions.updates import GetStateRequest

from . import db, outbox, raw
from .config import Config

log = logging.getLogger(__name__)

# Liveness. Telethon can lose its connection without raising: the handler
# just stops being called and run_until_disconnected() waits forever — that
# is how this listener went silent from 2026-10-06 to 2026-10-18 while
# systemd saw a healthy process. The watchdog asks Telegram for the update
# state on a timer; a timeout or error raises LiveStalled, the process exits
# non-zero and systemd restarts it.
WATCHDOG_INTERVAL_S = int(os.environ.get("TELETHON_WATCHDOG_INTERVAL_S", "120"))
WATCHDOG_TIMEOUT_S = int(os.environ.get("TELETHON_WATCHDOG_TIMEOUT_S", "45"))
HEARTBEAT_LOG_EVERY_S = 3600


class LiveStalled(RuntimeError):
    """The connection to Telegram stopped answering."""


async def heartbeat_once(client, timeout_s: float = WATCHDOG_TIMEOUT_S) -> None:
    """One liveness probe: a cheap authenticated request with a deadline."""
    if not client.is_connected():
        raise LiveStalled("client reports disconnected")
    try:
        await asyncio.wait_for(client(GetStateRequest()), timeout_s)
    except asyncio.TimeoutError as e:
        raise LiveStalled(f"no answer from Telegram within {timeout_s:.0f}s") from e
    except Exception as e:  # noqa: BLE001 — any RPC/transport failure counts
        raise LiveStalled(f"liveness probe failed: {type(e).__name__}: {e}") from e


async def watchdog(client, pool: asyncpg.Pool, counters: dict) -> None:
    """Probe on a timer; log a heartbeat once an hour (so a silent log is a
    symptom again, not the norm); stamp the source as ok on each success."""
    last_log = 0.0
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_S)
        await heartbeat_once(client)
        now = time.monotonic()
        if now - last_log >= HEARTBEAT_LOG_EVERY_S:
            log.info("live: heartbeat ok (events since start=%d)", counters.get("events", 0))
            last_log = now
        try:
            await db.mark_source_status(pool, "telegram", "ok", None)
        except Exception:  # noqa: BLE001 — the probe is what matters
            log.warning("live: could not stamp source status", exc_info=True)


async def run(client: TelegramClient, pool: asyncpg.Pool, cfg: Config) -> None:
    counters = {"events": 0}

    @client.on(events.NewMessage(incoming=True))
    @client.on(events.NewMessage(outgoing=True))
    async def _on_message(event: events.NewMessage.Event) -> None:
        msg = event.message
        counters["events"] += 1
        try:
            chat = await event.get_chat()
            is_private = isinstance(chat, types.User)

            # Group / channel ingestion is opt-in per chat via
            # raw.telegram_group_allowlist. On first sighting we record
            # the group (enabled=FALSE) so the UI can list it; we then
            # skip the message until the user opts in.
            #
            # The allowlist IS the filter for groups — cfg.chat_filter
            # is left in place (still defaults to 'private') for legacy
            # configs but is no longer consulted here. Private DMs are
            # always processed; groups need explicit enabled=TRUE.
            if not is_private:
                # Also stamps last_message_at = this message's date so the
                # /groups "Recently active" sort reflects the chat's
                # actual liveness (not our own scan cadence).
                await raw.upsert_group_allowlist_row(
                    pool, chat, last_message_at=msg.date,
                )
                # msg.chat_id is already in the signed form the allowlist
                # uses, so this lookup is direct (no entity normalization).
                if not await raw.is_group_enabled(pool, msg.chat_id):
                    return

            await raw.record_chat(pool, chat)
            if is_private:
                await raw.record_user(pool, chat)
            inserted = await raw.record_message(
                pool,
                client,
                msg,
                voice_dir=cfg.voice_dir,
                download_voice=cfg.download_voice,
            )
            log.info(
                "live: chat=%s kind_chat=%s msg=%s kind=%s new=%s",
                msg.chat_id,
                "private" if is_private else "group",
                msg.id,
                _kind(msg),
                inserted,
            )
        except Exception:
            log.exception("live handler failed for msg %s", msg.id)

    log.info("live: listening (filter=%s)", cfg.chat_filter)
    # Outbound send poller shares this connected client (the only safe way to
    # send — a 2nd client on the same session would clash). It sends drafts the
    # UI explicitly enqueued into memory.telegram_outbox.
    asyncio.create_task(outbox.poll_loop(client, pool))
    # Wait for either a real disconnect or the watchdog giving up. A stalled
    # connection raises LiveStalled out of here → non-zero exit → systemd
    # Restart=on-failure brings a fresh process up.
    waiter = asyncio.ensure_future(client.run_until_disconnected())
    dog = asyncio.ensure_future(watchdog(client, pool, counters))
    done, pending = await asyncio.wait({waiter, dog}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    for t in done:
        t.result()   # re-raises LiveStalled (or whatever ended the wait)


def _kind(msg) -> str:
    if msg.voice:
        return "voice"
    if msg.photo:
        return "photo"
    if msg.text:
        return "text"
    return "other"
