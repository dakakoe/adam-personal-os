from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Where to read aggregate health from. The merge API already computes
    # per-worker systemd status + a DB ping at /api/health/system, so we
    # reuse it rather than re-implementing the systemctl logic here.
    health_url: str
    bearer_token: str

    # Telegram bot delivery (decoupled from the always-on Telethon ingest
    # session — a bot has its own auth and cannot clash with it).
    tg_bot_token: str
    tg_chat_id: str

    # Optional direct credit probe: a 1-token Anthropic call that surfaces an
    # out-of-credits balance even when no LLM worker has run recently. This is
    # the project's single silent SPOF, so it's worth an explicit check.
    anthropic_api_key: str | None
    probe_anthropic: bool
    probe_model: str

    # Dedup / re-notify cadence.
    state_path: str
    renotify_hours: float

    # Shown at the bottom of every alert.
    dashboard_url: str

    timeout_s: float


def load() -> Config:
    return Config(
        health_url=os.environ.get(
            "ALERT_HEALTH_URL", "http://127.0.0.1:9100/api/health/system"
        ),
        bearer_token=_required("MERGE_API_BEARER_TOKEN"),
        tg_bot_token=_required("ALERT_TG_BOT_TOKEN"),
        tg_chat_id=_required("ALERT_TG_CHAT_ID"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        probe_anthropic=_bool_env("ALERT_ANTHROPIC_PROBE", default=True),
        probe_model=os.environ.get("ALERT_PROBE_MODEL", "claude-haiku-4-5"),
        state_path=os.environ.get("ALERT_STATE_PATH", "/srv/memory/state/alerter.json"),
        renotify_hours=float(os.environ.get("ALERT_RENOTIFY_HOURS", "6")),
        dashboard_url=os.environ.get("ALERT_DASHBOARD_URL", "https://merge.example.com/health"),
        timeout_s=float(os.environ.get("ALERT_TIMEOUT_S", "20")),
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
