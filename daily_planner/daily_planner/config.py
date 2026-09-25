from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str
    anthropic_api_key: str | None  # optional — fall back to a no-LLM plan
    model: str
    # Which day to plan for: 'auto' (tomorrow if it's evening, else today),
    # 'today', or 'tomorrow'. The nightly timer pins 'tomorrow'; the UI
    # regenerate button pins 'today'.
    target: str
    # IANA tz the plan_date + evening cutoff are computed in. the user lives
    # in your city; the droplet runs UTC — anchor explicitly (see digest).
    timezone: str
    # Hour-of-day (local) at/after which 'auto' rolls over to tomorrow.
    evening_hour: int
    # "Owes a reply" window: inbound with no outbound after, within N hours.
    reply_window_hours: int
    # Caps fed to the model (cost + focus guard).
    max_tasks: int
    max_opps: int
    max_owes_reply: int
    max_recaps: int
    self_label: str
    dry_run: bool       # build + print the plan but don't write the DB row


def load() -> Config:
    return Config(
        db_url=os.environ.get("PLANNER_DATABASE_URL") or _build_db_url(),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        model=os.environ.get("PLANNER_MODEL", "claude-haiku-4-5"),
        target=os.environ.get("PLANNER_TARGET", "auto").strip().lower(),
        timezone=os.environ.get("PLANNER_TZ", "Asia/Bangkok"),
        evening_hour=int(os.environ.get("PLANNER_EVENING_HOUR", "18")),
        reply_window_hours=int(os.environ.get("PLANNER_REPLY_WINDOW_HOURS", "48")),
        max_tasks=int(os.environ.get("PLANNER_MAX_TASKS", "25")),
        max_opps=int(os.environ.get("PLANNER_MAX_OPPS", "20")),
        max_owes_reply=int(os.environ.get("PLANNER_MAX_OWES_REPLY", "12")),
        max_recaps=int(os.environ.get("PLANNER_MAX_RECAPS", "6")),
        self_label=os.environ.get("PLANNER_SELF_LABEL", "the user"),
        dry_run=_bool_env("PLANNER_DRY_RUN", default=False),
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
