from __future__ import annotations

import asyncio
import logging

import asyncpg
from telethon import TelegramClient, events
from telethon.tl import types

from . import outbox, raw
from .config import Config

log = logging.getLogger(__name__)


async def run(client: TelegramClient, pool: asyncpg.Pool, cfg: Config) -> None:
    @client.on(events.NewMessage(incoming=True))
    @client.on(events.NewMessage(outgoing=True))
    async def _on_message(event: events.NewMessage.Event) -> None:
        msg = event.message
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
    # disconnected() blocks until the client disconnects; combined with
    # systemd Restart=on-failure this gives us automatic reconnect.
    await client.run_until_disconnected()


def _kind(msg) -> str:
    if msg.voice:
        return "voice"
    if msg.photo:
        return "photo"
    if msg.text:
        return "text"
    return "other"
