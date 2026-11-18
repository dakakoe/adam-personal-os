from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import health, notify
from .config import Config
from .state import State

log = logging.getLogger(__name__)


def _render_alert(report: health.HealthReport, dashboard_url: str) -> str:
    lines = ["🔴 memory-os degraded"]
    for p in report.problems:
        lines.append(f"• {p.label}: {p.detail}")
    lines.append(f"→ {dashboard_url}")
    return "\n".join(lines)


def _render_recovery(dashboard_url: str) -> str:
    return f"✅ memory-os recovered\nAll workers green again.\n→ {dashboard_url}"


def _should_alert(
    report: health.HealthReport, prev: State, now: datetime, renotify: timedelta
) -> bool:
    """Alert when we newly enter a degraded state, when the SET of problems
    changes, or when the same problems have persisted past the re-notify
    window (so a steady outage still nags but doesn't spam every tick)."""
    if not prev.degraded:
        return True
    if report.signature != prev.last_alert_signature:
        return True
    if prev.last_alert_at is None:
        return True
    return now - prev.last_alert_at >= renotify


def run(cfg: Config) -> int:
    now = datetime.now(timezone.utc)
    report = health.gather(cfg)
    prev = State.load(cfg.state_path)
    renotify = timedelta(hours=cfg.renotify_hours)

    # Exit code reflects whether the ALERTER did its job, not whether the
    # system is degraded — otherwise the alerter's own unit would flip to
    # "failed" on /health every time anything else broke (and self-loop if it
    # ever joins the watch list). A degraded system is reported via Telegram.
    delivery_failed = False

    if report.degraded:
        log.info("degraded: %s", report.signature)
        new_state = State(
            degraded=True,
            last_alert_signature=prev.last_alert_signature,
            last_alert_at=prev.last_alert_at,
        )
        if _should_alert(report, prev, now, renotify):
            if notify.send_telegram(cfg, _render_alert(report, cfg.dashboard_url)):
                new_state.last_alert_signature = report.signature
                new_state.last_alert_at = now
                log.info("alert sent")
            else:
                # Keep prev alert markers so the next run retries instead of
                # treating this as a delivered alert.
                delivery_failed = True
                log.error("alert delivery failed; will retry next run")
        else:
            log.info("degraded but within re-notify window; staying quiet")
        new_state.save(cfg.state_path)
        return 1 if delivery_failed else 0

    # Healthy now.
    if prev.degraded:
        log.info("recovered")
        if not notify.send_telegram(cfg, _render_recovery(cfg.dashboard_url)):
            delivery_failed = True
    else:
        log.info("healthy")
    State(degraded=False, last_alert_signature="", last_alert_at=None).save(cfg.state_path)
    return 1 if delivery_failed else 0
