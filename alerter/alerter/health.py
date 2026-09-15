from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from .config import Config

log = logging.getLogger(__name__)


@dataclass
class Problem:
    key: str       # stable id for dedup (e.g. unit name or "db" / "anthropic")
    label: str     # human label for the alert body
    detail: str    # short state string


@dataclass
class HealthReport:
    problems: list[Problem] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return bool(self.problems)

    @property
    def signature(self) -> str:
        # Sorted keys → stable across runs; changes only when the problem SET
        # changes, which is exactly when we want to re-alert.
        return ",".join(sorted(p.key for p in self.problems))


def _fmt_last_run(ts: str | None) -> str:
    # systemd timestamps look like "Sun 2026-06-01 03:12:04 UTC"; keep the
    # time-ish tail so the alert is glanceable without being noisy.
    if not ts:
        return ""
    return f" (last run {ts})"


def gather(cfg: Config) -> HealthReport:
    report = HealthReport()
    _check_system(cfg, report)
    if cfg.probe_anthropic:
        _probe_anthropic(cfg, report)
    return report


def _check_system(cfg: Config, report: HealthReport) -> None:
    """Pull the merge API's own health rollup. If the API itself is
    unreachable that IS the degradation — report it directly."""
    try:
        resp = httpx.get(
            cfg.health_url,
            headers={"Authorization": f"Bearer {cfg.bearer_token}"},
            timeout=cfg.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("health endpoint unreachable: %s", e)
        report.problems.append(
            Problem("merge-api-unreachable", "Merge API", "health endpoint unreachable")
        )
        return

    if data.get("db_ok") is False:
        report.problems.append(Problem("db", "Database", "unreachable"))

    for u in data.get("units") or []:
        if u.get("ok") is not False:  # True (healthy) or None (never run) → skip
            continue
        label = u.get("label") or u.get("unit") or "unit"
        # The API now sends a precise `reason` (exit N / schedule stopped /
        # overdue / inactive); fall back to the old derivation if it's absent.
        detail = u.get("reason")
        if not detail:
            if u.get("kind") == "always_on":
                detail = u.get("active_state") or "inactive"
            else:
                exit_status = u.get("exit_status")
                detail = f"exit {exit_status}" if exit_status is not None else "failed"
                detail += _fmt_last_run(u.get("last_run"))
        report.problems.append(Problem(u.get("unit") or label, label, detail))


def _probe_anthropic(cfg: Config, report: HealthReport) -> None:
    """A 1-token call that turns the project's silent SPOF — an out-of-credits
    Anthropic balance — into an explicit signal. Only a credit/billing error
    counts as degraded; transient/network errors are logged but ignored so we
    don't false-alarm."""
    if not cfg.anthropic_api_key:
        return
    import anthropic

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    try:
        client.messages.create(
            model=cfg.probe_model,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
    except Exception as e:  # noqa: BLE001 — classify by message, not type
        msg = str(e).lower()
        if "credit" in msg or "billing" in msg or "quota" in msg:
            report.problems.append(
                Problem("anthropic-credits", "Anthropic", "credit balance low / out of credits")
            )
        else:
            log.warning("anthropic probe failed (non-credit, ignoring): %s", e)
