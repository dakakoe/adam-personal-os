from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str
    # The narrative runs on LOCAL Ollama (sovereignty routing) — the digest
    # payload is raw message text and never goes to a cloud provider.
    ollama_url: str
    ollama_model: str
    self_label: str
    output_dir: str
    window_hours: int              # how far back the digest covers


def load() -> Config:
    return Config(
        db_url=os.environ.get("DIGEST_DATABASE_URL") or _build_db_url(),
        ollama_url=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
        ollama_model=os.environ.get("DIGEST_OLLAMA_MODEL") or os.environ.get("OLLAMA_MODEL") or "qwen2.5:3b",
        self_label=os.environ.get("DIGEST_SELF_LABEL", "me"),
        output_dir=os.environ.get("DIGEST_OUTPUT_DIR", "/srv/memory/logs"),
        window_hours=int(os.environ.get("DIGEST_WINDOW_HOURS", "24")),
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
