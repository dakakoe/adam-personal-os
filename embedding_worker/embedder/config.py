from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str
    model_name: str       # HF id, e.g. 'intfloat/multilingual-e5-base'
    cache_dir: str        # where sentence-transformers stashes downloaded models
    cpu_threads: int      # set on torch + sentence-transformers
    batch_size: int       # rows per fetch; same value used as the encode batch
    max_chars: int        # truncate `body` before encoding (model's 512-token cap)
    poll_interval_sec: int
    # Also embed raw.gmail_message into memory.mail_embedding (Ollama
    # expansion) — mail drains only when the interaction queue is empty.
    mail_enabled: bool


def load() -> Config:
    return Config(
        db_url=os.environ.get("EMBEDDER_DATABASE_URL") or _build_db_url(),
        model_name=os.environ.get("EMBEDDER_MODEL", "intfloat/multilingual-e5-base"),
        cache_dir=os.environ.get("EMBEDDER_MODEL_CACHE", "/srv/memory/data/embedding-models"),
        # Conservative; Whisper is using cpu_threads=2 concurrently. Set
        # higher when Whisper backlog is done.
        cpu_threads=int(os.environ.get("EMBEDDER_CPU_THREADS", "1")),
        batch_size=int(os.environ.get("EMBEDDER_BATCH_SIZE", "32")),
        # e5 models have a 512-token context; ~2000 chars covers most cases
        # before the tokenizer truncates anyway, and stops us from pinning
        # memory on pathologically long messages.
        max_chars=int(os.environ.get("EMBEDDER_MAX_CHARS", "2000")),
        poll_interval_sec=int(os.environ.get("EMBEDDER_POLL_INTERVAL", "60")),
        mail_enabled=os.environ.get("EMBEDDER_MAIL_ENABLED", "1").strip().lower()
            not in {"0", "false", "no", "off"},
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
