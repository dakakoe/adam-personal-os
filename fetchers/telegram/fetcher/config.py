from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    phone: str
    session_path: str
    db_url: str
    voice_dir: str
    backfill_days: int  # 0 = no cutoff, walk to the very first message
    chat_filter: str    # 'private' | 'all'
    download_voice: bool


def load() -> Config:
    return Config(
        api_id=int(_required("TELEGRAM_API_ID")),
        api_hash=_required("TELEGRAM_API_HASH"),
        phone=_required("TELEGRAM_PHONE"),
        session_path=os.environ.get(
            "TELETHON_SESSION_PATH",
            "/srv/memory/apps/telethon/session/memory",
        ),
        db_url=os.environ.get("TELETHON_DATABASE_URL") or _build_db_url(),
        voice_dir=os.environ.get("TELETHON_VOICE_DIR", "/srv/memory/data/voice"),
        backfill_days=int(os.environ.get("TELETHON_BACKFILL_DAYS", "7")),
        chat_filter=os.environ.get("TELETHON_CHAT_FILTER", "private"),
        download_voice=_bool_env("TELETHON_DOWNLOAD_VOICE", default=True),
    )


def _bool_env(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(f"missing required env: {name}")
    return val


def _build_db_url() -> str:
    user = _required("POSTGRES_USER")
    pw = _required("POSTGRES_PASSWORD")
    db = _required("POSTGRES_DB")
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgres://{user}:{pw}@{host}:{port}/{db}"
