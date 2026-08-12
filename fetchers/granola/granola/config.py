from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Granola official API (Settings → Connectors → API keys → grn_…).
    # Optional at load time: the key is resolved at run start — the setup
    # wizard's stored key (fetched from merge_api over the bearer channel,
    # this worker is deliberately DB-less) wins over the env var, so a key
    # rotated in the UI takes effect without touching .env.
    api_key: str | None
    key_url: str
    api_base: str
    # The merge ingest endpoint (same one the old Cowork relay POSTed to).
    # Defaults to localhost since this runs on the droplet alongside merge_api.
    ingest_url: str
    ingest_token: str
    # Where this worker reports its own health for the Sources page; same
    # host/token as ingest (this worker has no DB connection of its own).
    status_url: str
    # Re-list notes created in this trailing window each run. The ingest
    # endpoint is idempotent (skips already-processed notes without an LLM
    # call), so a wide window is cheap and tolerates a note whose AI summary
    # is generated a little late.
    window_days: int
    page_size: int
    # Polite pause between Granola detail calls (rate limit: 5 req/s sustained).
    request_pause_s: float
    dry_run: bool


def load() -> Config:
    return Config(
        api_key=os.environ.get("GRANOLA_API_KEY") or None,
        key_url=os.environ.get(
            "MERGE_KEY_URL", "http://127.0.0.1:9100/api/internal/source-secret/granola"),
        api_base=os.environ.get("GRANOLA_API_BASE", "https://public-api.granola.ai/v1").rstrip("/"),
        ingest_url=os.environ.get("MERGE_INGEST_URL", "http://127.0.0.1:9100/api/ingest/granola"),
        ingest_token=_required("MERGE_API_BEARER_TOKEN"),
        status_url=os.environ.get("MERGE_STATUS_URL", "http://127.0.0.1:9100/api/sources/granola/status"),
        window_days=int(os.environ.get("GRANOLA_WINDOW_DAYS", "7")),
        page_size=int(os.environ.get("GRANOLA_PAGE_SIZE", "30")),
        request_pause_s=float(os.environ.get("GRANOLA_REQUEST_PAUSE_S", "0.25")),
        dry_run=_bool_env("GRANOLA_DRY_RUN", default=False),
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
