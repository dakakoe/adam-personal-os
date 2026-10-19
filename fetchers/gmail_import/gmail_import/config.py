from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str
    mbox_path: str
    account_email: str
    skip_spam: bool
    skip_trash: bool
    batch_size: int
    progress_every: int
    dry_run: bool
    limit: int | None     # cap messages per run; None = all


def load() -> Config:
    return Config(
        db_url=os.environ.get("GMAIL_IMPORT_DATABASE_URL") or _build_db_url(),
        mbox_path=_required("GMAIL_IMPORT_MBOX"),
        account_email=_required("GMAIL_IMPORT_ACCOUNT").lower(),
        skip_spam=_bool_env("GMAIL_IMPORT_SKIP_SPAM", default=True),
        skip_trash=_bool_env("GMAIL_IMPORT_SKIP_TRASH", default=False),
        batch_size=int(os.environ.get("GMAIL_IMPORT_BATCH", "200")),
        progress_every=int(os.environ.get("GMAIL_IMPORT_PROGRESS_EVERY", "1000")),
        dry_run=_bool_env("GMAIL_IMPORT_DRY_RUN", default=False),
        limit=(int(v) if (v := os.environ.get("GMAIL_IMPORT_LIMIT")) else None),
    )


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(f"missing required env: {name}")
    return val


def _bool_env(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _build_db_url() -> str:
    user = _required("POSTGRES_USER")
    pw = _required("POSTGRES_PASSWORD")
    db = _required("POSTGRES_DB")
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgres://{user}:{pw}@{host}:{port}/{db}"
