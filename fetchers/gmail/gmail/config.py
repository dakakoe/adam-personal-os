from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str
    client_secrets_path: str    # path to credentials.json from Google Cloud Console
    scopes: tuple[str, ...]
    sync_messages_per_account: int    # cap per sync run; full history is fetched across many runs
    sync_initial_days: int            # for new accounts, how far back to walk on first sync
    # Calendar sync window (Phase 4 v2): pull events from now-back .. now+ahead.
    # The planner only needs the next couple of days; we keep a small back
    # window so an event that just ended still shows context.
    calendar_back_days: int
    calendar_ahead_days: int


def load() -> Config:
    return Config(
        db_url=os.environ.get("GMAIL_DATABASE_URL") or _build_db_url(),
        client_secrets_path=os.environ.get(
            "GMAIL_CLIENT_SECRETS", "/srv/memory/secrets/gmail-client-secrets.json"
        ),
        scopes=(
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/contacts.readonly",
            "https://www.googleapis.com/auth/contacts.other.readonly",
            "https://www.googleapis.com/auth/calendar.readonly",
        ),
        sync_messages_per_account=int(os.environ.get("GMAIL_SYNC_PER_ACCOUNT", "500")),
        sync_initial_days=int(os.environ.get("GMAIL_INITIAL_DAYS", "3650")),  # ~10 years
        calendar_back_days=int(os.environ.get("GMAIL_CALENDAR_BACK_DAYS", "1")),
        calendar_ahead_days=int(os.environ.get("GMAIL_CALENDAR_AHEAD_DAYS", "14")),
    )


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
