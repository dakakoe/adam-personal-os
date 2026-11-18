from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str
    anthropic_api_key: str
    model: str
    # Max staleness: ignore threads with no activity in this many days. A
    # thread is a candidate when its newest message is both within this
    # window AND newer than its scan watermark. (Replaces the old fixed 48h
    # lookback — kept as `lookback_hours` only for backward-compat env.)
    seed_days: int
    lookback_hours: int
    # Cap person-threads processed per run (cost guard). Un-scanned threads
    # remain candidates next run — the cap delays, never drops.
    max_threads: int
    # Recent messages per thread fed to the model.
    messages_per_thread: int
    self_label: str
    # Trigram-similarity backstop for dedup: a candidate whose title is at
    # least this similar to an existing suggestion/task for the same person
    # is dropped as a near-exact re-proposal. The model also gets the
    # already-tracked list for semantic dedup (catches paraphrases this
    # lexical score misses).
    dedup_similarity: float
    # Max already-tracked titles fed to the model per person (prompt budget).
    max_existing_items: int
    dry_run: bool
    # Local Ollama for sensitive contacts (their messages never go to cloud).
    ollama_url: str
    ollama_model: str
    ollama_timeout: float


def load() -> Config:
    return Config(
        db_url=os.environ.get("SCANNER_DATABASE_URL") or _build_db_url(),
        anthropic_api_key=_required("ANTHROPIC_API_KEY"),
        model=os.environ.get("SCANNER_MODEL", "claude-haiku-4-5"),
        seed_days=int(os.environ.get("SCANNER_SEED_DAYS", "14")),
        lookback_hours=int(os.environ.get("SCANNER_LOOKBACK_HOURS", "48")),
        max_threads=int(os.environ.get("SCANNER_MAX_THREADS", "40")),
        messages_per_thread=int(os.environ.get("SCANNER_MESSAGES_PER_THREAD", "20")),
        self_label=os.environ.get("SCANNER_SELF_LABEL", "the user (me)"),
        dedup_similarity=float(os.environ.get("SCANNER_DEDUP_SIMILARITY", "0.4")),
        max_existing_items=int(os.environ.get("SCANNER_MAX_EXISTING_ITEMS", "40")),
        dry_run=_bool_env("SCANNER_DRY_RUN", default=False),
        ollama_url=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
        ollama_model=os.environ.get("SCANNER_OLLAMA_MODEL") or os.environ.get("OLLAMA_MODEL") or "qwen2.5:3b",
        # Generous: a ~10k-char thread prompt on a busy 4-vCPU box can take
        # minutes to prefill; fail-closed means a timeout = never scanned.
        ollama_timeout=float(os.environ.get("SCANNER_OLLAMA_TIMEOUT", "360")),
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
