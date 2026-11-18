from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from telethon import errors

from . import (
    auth_steps,
    backfill,
    backfill_one,
    client as client_mod,
    config,
    db,
    download_avatars,
    download_voice,
    enrich_bios,
    live,
    raw,
)

log = logging.getLogger(__name__)


def _harden_umask() -> None:
    """Every file we create (session, voice .ogg) lands 0600/0700.
    Telegram session contains auth-token-equivalent state; do not let
    a forgotten umask leak it to other local accounts."""
    os.umask(0o077)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


async def cmd_auth() -> int:
    cfg = config.load()
    tg = client_mod.make_client(cfg)
    await client_mod.ensure_authorized(tg, cfg.phone)
    me = await tg.get_me()
    print(
        f"OK — logged in as {me.first_name or ''} {me.last_name or ''} "
        f"(@{me.username}, id={me.id}). Session: {cfg.session_path}.session"
    )
    await tg.disconnect()
    return 0


async def cmd_backfill() -> int:
    cfg = config.load()
    tg = client_mod.make_client(cfg)
    await client_mod.ensure_authorized(tg, cfg.phone)
    pool = await db.connect(cfg.db_url)
    try:
        await backfill.run(tg, pool, cfg)
    finally:
        await pool.close()
        await tg.disconnect()
    return 0


async def cmd_live() -> int:
    cfg = config.load()
    tg = client_mod.make_client(cfg)
    pool = await db.connect(cfg.db_url)
    try:
        # The live service has no TTY, so we must NOT use ensure_authorized()
        # (it would prompt via input() and crash). Check non-interactively: a
        # revoked/expired session → flag it for the Sources page and exit
        # non-zero. systemd Restart=on-failure retries; re-login is the manual
        # `auth` command on the droplet.
        await tg.connect()
        if not await tg.is_user_authorized():
            await db.mark_source_status(
                pool, "telegram", "needs_attention",
                "Telethon session is not authorized — run `python -m fetcher auth` "
                "on the droplet to log in again",
            )
            log.error("live: telegram session not authorized; flagged needs_attention")
            return 1
        await db.mark_source_status(pool, "telegram", "ok", None)
        await live.run(tg, pool, cfg)
        return 0
    except errors.UnauthorizedError as e:
        # Session revoked / auth key unregistered / account deactivated mid-run.
        await db.mark_source_status(
            pool, "telegram", "needs_attention",
            f"Telethon session lost ({type(e).__name__}) — re-run the `auth` command "
            "on the droplet",
        )
        log.error("live: telegram auth lost mid-run (%r); flagged needs_attention", e)
        return 1
    finally:
        await pool.close()
        await tg.disconnect()


async def cmd_download_voice() -> int:
    cfg = config.load()
    tg = client_mod.make_client(cfg)
    await client_mod.ensure_authorized(tg, cfg.phone)
    pool = await db.connect(cfg.db_url)
    try:
        await download_voice.run(tg, pool, cfg)
    finally:
        await pool.close()
        await tg.disconnect()
    return 0


async def cmd_download_avatars() -> int:
    """download-avatars uses the same session-copy trick as enrich-bios:
    the live memory-telethon service holds the SQLite session write lock,
    so we work off a temp copy and discard it at the end. download_profile_photo
    is a read-only API call as far as our session state goes — the entity
    cache updates we lose just trigger a fresh resolve in the live session."""
    import shutil
    import tempfile
    from pathlib import Path
    from dataclasses import replace

    cfg = config.load()
    src = Path(cfg.session_path + ".session")
    if not src.exists():
        print(f"error: session file not found at {src}", flush=True)
        return 2

    with tempfile.TemporaryDirectory(prefix="download-avatars-") as tmpdir:
        tmp_session_base = str(Path(tmpdir) / "memory")
        shutil.copy2(src, tmp_session_base + ".session")
        cfg_tmp = replace(cfg, session_path=tmp_session_base)
        tg = client_mod.make_client(cfg_tmp)
        await client_mod.ensure_authorized(tg, cfg_tmp.phone)
        pool = await db.connect(cfg_tmp.db_url)
        try:
            await download_avatars.run(tg, pool, cfg_tmp)
        finally:
            await pool.close()
            await tg.disconnect()
    return 0


async def cmd_backfill_one() -> int:
    """Backfill messages for ONE Telegram chat. Driven by env vars so
    callers (the merge_api subprocess) don't need to thread argparse:
      BACKFILL_ONE_CHAT_ID   — required, the Telegram peer id (negative
                                for groups, ~`-100…` for supergroups)
      BACKFILL_ONE_SINCE_DAYS — optional cap; default = full history
    Same session-copy trick as discover-groups."""
    import os
    import shutil
    import tempfile
    from pathlib import Path
    from dataclasses import replace

    chat_id_raw = os.environ.get("BACKFILL_ONE_CHAT_ID")
    if not chat_id_raw:
        print("error: BACKFILL_ONE_CHAT_ID env required", flush=True)
        return 2
    try:
        chat_id = int(chat_id_raw)
    except ValueError:
        print(f"error: BACKFILL_ONE_CHAT_ID must be int, got {chat_id_raw!r}", flush=True)
        return 2
    since_raw = os.environ.get("BACKFILL_ONE_SINCE_DAYS")
    since_days = int(since_raw) if since_raw else None

    cfg = config.load()
    src = Path(cfg.session_path + ".session")
    if not src.exists():
        print(f"error: session file not found at {src}", flush=True)
        return 2

    with tempfile.TemporaryDirectory(prefix="backfill-one-") as tmpdir:
        tmp_session_base = str(Path(tmpdir) / "memory")
        shutil.copy2(src, tmp_session_base + ".session")
        cfg_tmp = replace(cfg, session_path=tmp_session_base)
        tg = client_mod.make_client(cfg_tmp)
        await client_mod.ensure_authorized(tg, cfg_tmp.phone)
        pool = await db.connect(cfg_tmp.db_url)
        try:
            result = await backfill_one.run(
                tg, pool, cfg_tmp, chat_id=chat_id, since_days=since_days,
            )
            # Emit a final summary line that the merge_api subprocess
            # caller can grep / parse if it wants exact counts.
            print(f"backfill_one_result seen={result['seen']} new={result['new']}", flush=True)
        finally:
            await pool.close()
            await tg.disconnect()
    return 0


async def cmd_backfill_enabled() -> int:
    """Backfill history for EVERY enabled group that has no messages yet
    (or all enabled groups with BACKFILL_ALL=1), one after another on a
    single session copy — the way to catch up after auto-follow turned on
    many groups at once. Env:
      BACKFILL_SINCE_DAYS       cap per chat (default 730)
      BACKFILL_ACTIVE_WITHIN    skip chats whose last message is older than
                                this many days (default 730; 0 = no skip)
      BACKFILL_ALL=1            include chats that already have messages
    Resumable: rerun and it continues with what is still missing."""
    import os
    import shutil
    import tempfile
    from pathlib import Path
    from dataclasses import replace
    since_days = int(os.environ.get("BACKFILL_SINCE_DAYS", "730"))
    active_within = int(os.environ.get("BACKFILL_ACTIVE_WITHIN", "730"))
    include_done = os.environ.get("BACKFILL_ALL") == "1"
    cfg = config.load()
    src = Path(cfg.session_path + ".session")
    if not src.exists():
        print(f"error: session file not found at {src}", flush=True)
        return 2
    with tempfile.TemporaryDirectory(prefix="backfill-enabled-") as tmpdir:
        tmp_session_base = str(Path(tmpdir) / "memory")
        shutil.copy2(src, tmp_session_base + ".session")
        cfg_tmp = replace(cfg, session_path=tmp_session_base)
        tg = client_mod.make_client(cfg_tmp)
        await client_mod.ensure_authorized(tg, cfg_tmp.phone)
        pool = await db.connect(cfg_tmp.db_url)
        try:
            rows = await pool.fetch(
                """
                SELECT a.chat_id, a.title
                  FROM raw.telegram_group_allowlist a
                 WHERE a.enabled AND a.dismissed_at IS NULL
                   AND ($1::bool OR NOT EXISTS (SELECT 1 FROM raw.telegram_message m WHERE m.chat_id = a.chat_id))
                   AND ($2::int = 0 OR a.last_message_at IS NULL
                        OR a.last_message_at > now() - ($2::int * interval '1 day'))
                 ORDER BY a.last_message_at DESC NULLS LAST
                """, include_done, active_within)
            log.info("backfill-enabled: %d chats to do (since_days=%d)", len(rows), since_days)
            seen = new = done = 0
            for r in rows:
                try:
                    res = await backfill_one.run(tg, pool, cfg_tmp, chat_id=r["chat_id"], since_days=since_days)
                except Exception:  # noqa: BLE001 — one dead chat must not stop the rest
                    log.exception("backfill-enabled: chat %s failed", r["chat_id"])
                    continue
                seen += res["seen"]; new += res["new"]; done += 1
                log.info("backfill-enabled: %d/%d chats, msgs seen=%d new=%d", done, len(rows), seen, new)
            print(f"backfill_enabled_result chats={done} seen={seen} new={new}", flush=True)
        finally:
            await pool.close()
            await tg.disconnect()
    return 0


async def cmd_record_group_members() -> int:
    """One-off repair: for every followed group that has message senders we
    have never recorded as users, pull the member list and record them, so
    the normalizer makes people of them and the group page shows names
    instead of bare ids. Session-copy trick like the other batch commands."""
    import shutil
    import tempfile
    from pathlib import Path
    from dataclasses import replace
    from telethon.tl import types
    cfg = config.load()
    src = Path(cfg.session_path + ".session")
    if not src.exists():
        print(f"error: session file not found at {src}", flush=True)
        return 2
    with tempfile.TemporaryDirectory(prefix="record-members-") as tmpdir:
        tmp_session_base = str(Path(tmpdir) / "memory")
        shutil.copy2(src, tmp_session_base + ".session")
        cfg_tmp = replace(cfg, session_path=tmp_session_base)
        tg = client_mod.make_client(cfg_tmp)
        await client_mod.ensure_authorized(tg, cfg_tmp.phone)
        pool = await db.connect(cfg_tmp.db_url)
        try:
            chats = await pool.fetch(
                """
                SELECT a.chat_id, count(DISTINCT m.sender_id) AS unknown
                  FROM raw.telegram_group_allowlist a
                  JOIN raw.telegram_message m ON m.chat_id = a.chat_id
                  LEFT JOIN raw.telegram_user u ON u.source_user_id = m.sender_id
                 WHERE a.enabled AND m.sender_id > 0 AND u.source_user_id IS NULL
                 GROUP BY a.chat_id ORDER BY unknown DESC
                """)
            log.info("record-group-members: %d chats with unknown senders", len(chats))
            recorded = 0
            seen: set = set()
            for row in chats:
                try:
                    entity = await tg.get_entity(row["chat_id"])
                    async for u in tg.iter_participants(entity, limit=1500):
                        if isinstance(u, types.User) and u.id not in seen:
                            seen.add(u.id)
                            await raw.record_user(pool, u)
                            recorded += 1
                except Exception as e:  # noqa: BLE001 — left the group, no rights, flood wait
                    log.warning("record-group-members: chat %s skipped (%s)", row["chat_id"], type(e).__name__)
            still = await pool.fetchval(
                """
                SELECT count(DISTINCT m.sender_id) FROM raw.telegram_message m
                  JOIN raw.telegram_group_allowlist a ON a.chat_id = m.chat_id AND a.enabled
                  LEFT JOIN raw.telegram_user u ON u.source_user_id = m.sender_id
                 WHERE m.sender_id > 0 AND u.source_user_id IS NULL
                """)
            print(f"record_group_members_result chats={len(chats)} users_recorded={recorded} senders_still_unknown={still}", flush=True)
        finally:
            await pool.close()
            await tg.disconnect()
    return 0


async def cmd_discover_groups() -> int:
    """Walk client.iter_dialogs() and seed raw.telegram_group_allowlist
    with every group / supergroup / channel the user is in. enabled
    stays FALSE — the allowlist UI flips that per-row. Uses the same
    session-copy trick as enrich-bios since live holds the SQLite lock."""
    import shutil
    import tempfile
    from pathlib import Path
    from dataclasses import replace
    from telethon.tl import types

    from . import raw

    cfg = config.load()
    src = Path(cfg.session_path + ".session")
    if not src.exists():
        print(f"error: session file not found at {src}", flush=True)
        return 2

    with tempfile.TemporaryDirectory(prefix="discover-groups-") as tmpdir:
        tmp_session_base = str(Path(tmpdir) / "memory")
        shutil.copy2(src, tmp_session_base + ".session")
        cfg_tmp = replace(cfg, session_path=tmp_session_base)
        tg = client_mod.make_client(cfg_tmp)
        await client_mod.ensure_authorized(tg, cfg_tmp.phone)
        pool = await db.connect(cfg_tmp.db_url)
        try:
            seen = 0
            async for dialog in tg.iter_dialogs():
                entity = dialog.entity
                if isinstance(entity, types.User):
                    continue
                # dialog.date is the chat's last-message timestamp per
                # Telethon (often == dialog.message.date when there is
                # a message). None for empty chats.
                last_msg = getattr(dialog, "date", None)
                await raw.upsert_group_allowlist_row(
                    pool, entity, last_message_at=last_msg,
                )
                seen += 1
            print(f"discovered {seen} groups/channels", flush=True)
        finally:
            await pool.close()
            await tg.disconnect()
    return 0


async def cmd_enrich_bios() -> int:
    """enrich-bios needs exclusive write access to the Telethon SQLite
    session. The long-running memory-telethon service already holds that
    lock, so we work off a temp copy and discard it at the end. GetFullUser
    is read-only on our side; any entity-cache updates that get lost just
    mean the next live session re-resolves them."""
    import shutil
    import tempfile
    from pathlib import Path

    cfg = config.load()
    src = Path(cfg.session_path + ".session")
    if not src.exists():
        print(f"error: session file not found at {src}", flush=True)
        return 2

    with tempfile.TemporaryDirectory(prefix="enrich-bios-") as tmpdir:
        tmp_session_base = str(Path(tmpdir) / "memory")
        shutil.copy2(src, tmp_session_base + ".session")

        # Build a one-off Config with the temp session path so make_client
        # routes Telethon at the copy. Everything else stays the same.
        from dataclasses import replace
        cfg_tmp = replace(cfg, session_path=tmp_session_base)
        tg = client_mod.make_client(cfg_tmp)
        await client_mod.ensure_authorized(tg, cfg_tmp.phone)
        pool = await db.connect(cfg_tmp.db_url)
        try:
            await enrich_bios.run(tg, pool, cfg_tmp)
        finally:
            await pool.close()
            await tg.disconnect()
    return 0


def main() -> int:
    _harden_umask()
    _setup_logging()
    parser = argparse.ArgumentParser(prog="fetcher")
    parser.add_argument(
        "command",
        nargs="?",
        default="live",
        choices=["auth", "auth-send-code", "auth-sign-in", "backfill", "live", "download-voice", "enrich-bios", "download-avatars", "discover-groups", "backfill-one", "backfill-enabled", "record-group-members"],
        help="What to do (default: live, used by systemd)",
    )
    args = parser.parse_args()

    handler = {
        "auth": cmd_auth,
        # Setup-wizard steps (env-driven, marker-line protocol — see auth_steps)
        "auth-send-code": auth_steps.send_code,
        "auth-sign-in": auth_steps.sign_in,
        "backfill": cmd_backfill,
        "backfill-enabled": cmd_backfill_enabled,
        "record-group-members": cmd_record_group_members,
        "live": cmd_live,
        "download-voice": cmd_download_voice,
        "enrich-bios": cmd_enrich_bios,
        "download-avatars": cmd_download_avatars,
        "discover-groups": cmd_discover_groups,
        "backfill-one": cmd_backfill_one,
    }[args.command]
    return asyncio.run(handler())


if __name__ == "__main__":
    raise SystemExit(main())
