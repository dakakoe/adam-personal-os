from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str
    self_user_id: int
    batch_size: int


def load() -> Config:
    return Config(
        db_url=os.environ.get("NORMALIZER_DATABASE_URL") or _build_db_url(),
        self_user_id=int(_required("TELEGRAM_SELF_USER_ID")),
        batch_size=int(os.environ.get("NORMALIZER_BATCH_SIZE", "5000")),
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
