from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str
    dry_run: bool
    limit: int | None      # cap people processed; None = all
    sample_chars: int      # how many chars of surrounding text to store as evidence
    self_emails: tuple[str, ...]   # addresses that belong to the user; never participate in identity merges


def load() -> Config:
    return Config(
        db_url=os.environ.get("ENRICHMENT_DATABASE_URL") or _build_db_url(),
        dry_run=_bool_env("ENRICHMENT_DRY_RUN", default=False),
        limit=(int(v) if (v := os.environ.get("ENRICHMENT_LIMIT")) else None),
        sample_chars=int(os.environ.get("ENRICHMENT_SAMPLE_CHARS", "120")),
        self_emails=tuple(
            s.strip().lower()
            for s in os.environ.get("ENRICHMENT_SELF_EMAILS", "").split(",")
            if s.strip()
        ),
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
