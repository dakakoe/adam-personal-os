"""Setup-wizard helpers (pure — the routes in app.py stay thin).

The Telegram auth flow subprocesses the telethon fetcher's `auth-send-code` /
`auth-sign-in` commands and parses the stable marker lines they print, the
same protocol as `backfill_one_result`. All parsing lives here so it's
testable without a subprocess or a Telegram account.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Sources whose secret may be stored via POST /api/setup/secrets/{key}.
# Extend when a new connector needs a UI-settable secret.
SECRET_SOURCES = {"granola"}

# Upload guard for the LinkedIn export.
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
_UPLOAD_NAME_RE = re.compile(r"^[\w][\w .()-]{0,120}\.(zip|csv)$", re.IGNORECASE)


@dataclass(frozen=True)
class AuthResult:
    """Normalized outcome of a telethon auth subprocess."""
    status: str                      # see _MARKERS below
    phone_code_hash: str | None = None
    retry_after_s: int | None = None
    user: str | None = None


# Marker lines the fetcher prints (fetchers/telegram/fetcher/auth_steps.py).
_MARKERS = (
    "already_authorized", "send_code_result", "need_2fa", "flood_wait",
    "bad_code", "code_expired", "bad_password", "bad_phone", "signed_in",
)


def parse_auth_output(stdout: str) -> AuthResult:
    """Last marker line wins (log noise may precede it). Unknown output →
    status='unknown' so the route can 500 with the tail."""
    result = AuthResult(status="unknown")
    for line in (stdout or "").splitlines():
        line = line.strip()
        marker = next((m for m in _MARKERS if line.startswith(m)), None)
        if marker is None:
            continue
        if marker == "send_code_result":
            m = re.search(r"phone_code_hash=(\S+)", line)
            result = AuthResult(status="code_sent",
                                phone_code_hash=m.group(1) if m else None)
        elif marker == "flood_wait":
            m = re.search(r"seconds=(\d+)", line)
            result = AuthResult(status="flood_wait",
                                retry_after_s=int(m.group(1)) if m else None)
        elif marker == "signed_in":
            m = re.search(r"signed_in\s+(.*)$", line)
            result = AuthResult(status="signed_in",
                                user=(m.group(1).strip() if m else None) or None)
        else:
            result = AuthResult(status=marker)
    return result


def parse_linkedin_output(stdout: str) -> dict | None:
    """Parse `linkedin_import_result connections=X messages=Y imported=Z`.
    None when the marker never appeared (caller reports the log tail)."""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("linkedin_import_result"):
            continue
        out = {}
        for key in ("connections", "messages", "imported"):
            m = re.search(rf"{key}=(\d+)", line)
            if m:
                out[key] = int(m.group(1))
        return out
    return None


def valid_upload_name(filename: str | None) -> bool:
    """Basename sanity for the LinkedIn export: .zip or .csv, no path parts."""
    if not filename:
        return False
    if "/" in filename or "\\" in filename or ".." in filename:
        return False
    return _UPLOAD_NAME_RE.match(filename) is not None
