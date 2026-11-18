"""Non-interactive, two-step Telegram auth for the setup wizard.

merge_api subprocesses these (the same pattern as backfill-one) and parses the
STABLE MARKER LINES printed to stdout — everything else goes to stderr logging.
The steps run as separate processes; that works because Telethon binds the
code request to the SESSION's auth key, which persists in the session file
between invocations, and phone_code_hash round-trips through the caller.

Markers (exit codes):
  already_authorized                    (0)
  send_code_result phone_code_hash=…    (0)
  signed_in id=… username=…             (0)
  need_2fa                              (3)
  bad_code | code_expired | bad_password | bad_phone   (2)
  flood_wait seconds=N                  (4)

AUTH_SESSION_PATH overrides the session file — used ONLY for prod-safe manual
testing with a throwaway session + spare phone number.

A session Telegram has terminated (from another device, or by Telegram itself)
leaves an auth key in the file that the server no longer knows; Telethon keeps
presenting it, so even auth.sendCode fails with AuthKeyUnregisteredError.
send_code retires such a file (kept beside it as .revoked-<stamp>) and retries
once with a fresh key, which is the ordinary first-login path.
"""
from __future__ import annotations

import logging
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from telethon.errors import (
    AuthKeyUnregisteredError,
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

from . import client as client_mod, config

log = logging.getLogger(__name__)


def _cfg():
    cfg = config.load()
    override = os.environ.get("AUTH_SESSION_PATH")
    if override:
        cfg = replace(cfg, session_path=override)
    return cfg


def session_file(session_path: str) -> Path:
    """The file Telethon actually opens: it appends .session unless the name
    already ends with it."""
    p = str(session_path)
    return Path(p if p.endswith(".session") else p + ".session")


def retire_session(session_path: str, now: datetime | None = None) -> Path | None:
    """Move a dead session file (and SQLite side files) aside, keeping it, so
    the next client negotiates a fresh auth key. None when there was none."""
    f = session_file(session_path)
    if not f.exists():
        return None
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    dest = f.with_name(f"{f.name}.revoked-{stamp}")
    f.rename(dest)
    for suffix in ("-journal", "-wal", "-shm"):
        side = Path(str(f) + suffix)
        if side.exists():
            side.rename(Path(str(dest) + suffix))
    return dest


async def send_code() -> int:
    cfg = _cfg()
    phone = os.environ.get("AUTH_PHONE") or cfg.phone
    for attempt in (1, 2):
        tg = client_mod.make_client(cfg)
        await tg.connect()
        try:
            if await tg.is_user_authorized():
                print("already_authorized", flush=True)
                return 0
            try:
                sent = await tg.send_code_request(phone)
            except FloodWaitError as e:
                print(f"flood_wait seconds={e.seconds}", flush=True)
                return 4
            except PhoneNumberInvalidError:
                print("bad_phone", flush=True)
                return 2
            except AuthKeyUnregisteredError:
                if attempt == 2:
                    raise
                # The server deleted this key (session terminated elsewhere).
                # Nothing works with it; start over with a fresh one.
                await tg.disconnect()
                backup = retire_session(cfg.session_path)
                log.warning("telegram auth key revoked; session retired to %s, retrying with a fresh key", backup)
                continue
            print(f"send_code_result phone_code_hash={sent.phone_code_hash}", flush=True)
            return 0
        finally:
            await tg.disconnect()
    return 1


async def sign_in() -> int:
    cfg = _cfg()
    phone = os.environ.get("AUTH_PHONE") or cfg.phone
    code = os.environ.get("AUTH_CODE", "").strip()
    phone_code_hash = os.environ.get("AUTH_PHONE_CODE_HASH", "").strip()
    password = os.environ.get("AUTH_PASSWORD") or None

    tg = client_mod.make_client(cfg)
    await tg.connect()
    try:
        if await tg.is_user_authorized():
            me = await tg.get_me()
            print(f"signed_in id={me.id} username={me.username or ''}", flush=True)
            return 0
        try:
            if password:
                # Resume-after-need_2fa: the pending-2FA state lives server-side
                # against this session's auth key, so password-only works. If
                # there is no pending state (fresh call with code+password),
                # fall through to the code path first.
                try:
                    await tg.sign_in(password=password)
                except Exception:
                    await tg.sign_in(phone=phone, code=code,
                                     phone_code_hash=phone_code_hash)
            else:
                await tg.sign_in(phone=phone, code=code,
                                 phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            if password:
                try:
                    await tg.sign_in(password=password)
                except Exception:
                    print("bad_password", flush=True)
                    return 2
            else:
                print("need_2fa", flush=True)
                return 3
        except PhoneCodeInvalidError:
            print("bad_code", flush=True)
            return 2
        except PhoneCodeExpiredError:
            print("code_expired", flush=True)
            return 2
        except FloodWaitError as e:
            print(f"flood_wait seconds={e.seconds}", flush=True)
            return 4

        me = await tg.get_me()
        print(f"signed_in id={me.id} username={me.username or ''}", flush=True)
        return 0
    finally:
        await tg.disconnect()
