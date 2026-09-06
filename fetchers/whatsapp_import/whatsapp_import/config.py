"""Env -> config. Same convention as every other worker: a per-worker
DATABASE_URL override, else built from the shared POSTGRES_* values.

Unlike the rest, this one runs on the Mac, so the default host is a local
SSH tunnel to the droplet rather than the droplet's own loopback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(
            f"missing required env: {name}\n"
            "Either set WHATSAPP_IMPORT_DATABASE_URL to a full connection string,\n"
            "or set POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB."
        )
    return val


def _build_db_url() -> str:
    user = _required("POSTGRES_USER")
    pw = _required("POSTGRES_PASSWORD")
    dbname = _required("POSTGRES_DB")
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    # 15432 by default: Postgres is bound to the droplet's loopback, so the
    # Mac reaches it through `ssh -N -L 15432:127.0.0.1:5432 memory`.
    port = os.environ.get("POSTGRES_PORT", "15432")
    return f"postgres://{user}:{pw}@{host}:{port}/{dbname}"


def load() -> Config:
    return Config(db_url=os.environ.get("WHATSAPP_IMPORT_DATABASE_URL") or _build_db_url())
