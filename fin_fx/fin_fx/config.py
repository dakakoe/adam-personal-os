from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str
    # Free, no-key fiat FX source (base=USD). rates[X] = X per 1 USD.
    fx_url: str
    dry_run: bool


def load() -> Config:
    return Config(
        db_url=os.environ.get("FIN_FX_DATABASE_URL") or _build_db_url(),
        fx_url=os.environ.get("FIN_FX_URL", "https://api.frankfurter.dev/v1/latest?from=USD"),
        dry_run=os.environ.get("FIN_FX_DRY_RUN", "").lower() in ("1", "true", "yes"),
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
