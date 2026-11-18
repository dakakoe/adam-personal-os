from __future__ import annotations
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Config:
    db_url: str
    timezone: str
    dry_run: bool

def load() -> Config:
    return Config(
        db_url=os.environ.get("FIN_PLANNED_DATABASE_URL") or _build_db_url(),
        timezone=os.environ.get("FIN_PLANNED_TZ", "Asia/Bangkok"),
        dry_run=os.environ.get("FIN_PLANNED_DRY_RUN", "").lower() in ("1", "true", "yes"),
    )

def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"missing required env: {name}")
    return v

def _build_db_url() -> str:
    return (f"postgres://{_required('POSTGRES_USER')}:{_required('POSTGRES_PASSWORD')}"
            f"@{os.environ.get('POSTGRES_HOST','127.0.0.1')}:{os.environ.get('POSTGRES_PORT','5432')}/{_required('POSTGRES_DB')}")
