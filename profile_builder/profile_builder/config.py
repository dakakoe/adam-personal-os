from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str
    anthropic_api_key: str
    model: str                      # claude-haiku-4-5 or override
    embedding_model: str
    embedding_cache_dir: str
    self_user_label: str            # used in prompts to refer to "me"
    messages_per_profile: int       # how many recent interactions to feed the LLM
    limit: int | None               # cap rows processed this run; None = all
    dry_run: bool
    rebuild: bool                   # if true, re-process people who already have a profile
    # Refresh-only mode: when set, processes ONLY existing profiles whose
    # last_built_at is older than this many days. Mutually exclusive with
    # rebuild (refresh wins; rebuild is ignored with a warning).
    refresh_older_than_days: int | None
    # Local Ollama for sensitive contacts (their messages never go to cloud).
    ollama_url: str
    ollama_model: str
    ollama_timeout: float


def load() -> Config:
    return Config(
        db_url=os.environ.get("PROFILE_DATABASE_URL") or _build_db_url(),
        anthropic_api_key=_required("ANTHROPIC_API_KEY"),
        model=os.environ.get("PROFILE_MODEL", "claude-haiku-4-5"),
        embedding_model=os.environ.get(
            "PROFILE_EMBEDDING_MODEL", "intfloat/multilingual-e5-base"
        ),
        embedding_cache_dir=os.environ.get(
            "PROFILE_EMBEDDING_CACHE", "/srv/memory/data/embedding-models"
        ),
        self_user_label=os.environ.get("PROFILE_SELF_LABEL", "me"),
        messages_per_profile=int(os.environ.get("PROFILE_MESSAGES_PER", "30")),
        limit=(int(v) if (v := os.environ.get("PROFILE_LIMIT")) else None),
        dry_run=_bool_env("PROFILE_DRY_RUN", default=False),
        rebuild=_bool_env("PROFILE_REBUILD", default=False),
        refresh_older_than_days=(
            int(v) if (v := os.environ.get("PROFILE_REFRESH_OLDER_THAN_DAYS")) else None
        ),
        ollama_url=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
        ollama_model=os.environ.get("PROFILE_OLLAMA_MODEL") or os.environ.get("OLLAMA_MODEL") or "qwen2.5:3b",
        # Generous: profile prompts carry ~30 messages; prefill on a busy
        # 4-vCPU box can take minutes; fail-closed means timeout = skipped.
        ollama_timeout=float(os.environ.get("PROFILE_OLLAMA_TIMEOUT", "360")),
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
