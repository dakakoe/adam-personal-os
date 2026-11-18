"""Google Calendar sync (Phase 4 v2).

For each authed account, list events in a small window around now
(now-back_days .. now+ahead_days) from the primary calendar, expand
recurrences (singleEvents=True), and upsert into raw.gcal_event. The daily
planner reads the plan-day slice of this table.

Requires the calendar.readonly scope on the account's refresh_token. If it
isn't there yet, events().list() returns 403 "insufficient authentication
scopes" — handled the same way contacts.py handles a missing contacts
scope: log a warning, mark the run as needing re-auth, continue.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from . import client as gclient, db
from .config import Config

log = logging.getLogger(__name__)


def _list_events(service, *, time_min: str, time_max: str) -> list[dict[str, Any]]:
    """Page through events.list on the primary calendar within the window."""
    out: list[dict[str, Any]] = []
    page_token = None
    while True:
        resp = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            showDeleted=False,
            maxResults=250,
            pageToken=page_token,
        ).execute()
        out.extend(resp.get("items") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            return out


async def sync_one_account(
    pool, cfg: Config, account_email: str, refresh_token: str,
) -> dict[str, int]:
    service = gclient.build_calendar_service(
        cfg.client_secrets_path, refresh_token, list(cfg.scopes)
    )
    now = datetime.now(timezone.utc)
    win_start = now - timedelta(days=cfg.calendar_back_days)
    win_end = now + timedelta(days=cfg.calendar_ahead_days)
    time_min = win_start.isoformat()
    time_max = win_end.isoformat()

    try:
        items = await asyncio.to_thread(
            _list_events, service, time_min=time_min, time_max=time_max
        )
    except HttpError as e:
        if e.resp.status in (401, 403):
            reason = gclient.http_error_reason(e)
            if gclient.is_reauth_error(e):
                # Revoked token or insufficient scope → user must re-consent.
                await db.mark_reauth_needed(
                    pool, account_email, f"calendar {e.resp.status} {reason}")
                log.warning(
                    "calendar %s: %d (%s) — flagged reauth_needed",
                    account_email, e.resp.status, reason or "forbidden",
                )
            elif reason == "accessNotConfigured":
                # Project-level: the Calendar API isn't enabled in the GCP
                # project — a one-time console toggle, NOT a scope problem.
                log.warning(
                    "calendar %s: 403 accessNotConfigured — enable the Calendar API "
                    "for the project, then this starts working", account_email,
                )
            else:
                # Rate/quota or other transient 403 — not a reconnect issue.
                log.warning(
                    "calendar %s: %d (%s)",
                    account_email, e.resp.status, reason or "forbidden",
                )
            return {"fetched": 0, "written": 0, "scope_missing": 1}
        raise
    except RefreshError as e:
        await db.mark_reauth_needed(pool, account_email, f"calendar refresh: {e!r}")
        log.warning("calendar %s: refresh failed — flagged reauth_needed (%r)",
                    account_email, e)
        return {"fetched": 0, "written": 0, "scope_missing": 1}

    rows = []
    for ev in items:
        row = gclient.api_event_to_row(
            account_email=account_email, calendar_id="primary", ev=ev
        )
        if row is not None:
            rows.append(row)
    written = await db.upsert_events(pool, rows)
    # Reconcile: soft-cancel mirror rows in this window Google no longer returns
    # (deleted/moved events). Keep-set is EVERY id Google returned, so an event
    # we chose not to store still shields its (absent) row from cancellation.
    keep_ids = [ev["id"] for ev in items if ev.get("id")]
    cancelled = await db.cancel_missing_events(
        pool, account_email, "primary", win_start, win_end, keep_ids
    )
    log.info("calendar %s: fetched=%d written=%d cancelled=%d",
             account_email, len(items), written, cancelled)
    return {"fetched": len(items), "written": written, "cancelled": cancelled, "scope_missing": 0}


async def run(cfg: Config) -> int:
    pool = await db.connect(cfg.db_url)
    try:
        accounts = await db.list_active_accounts(pool)
        log.info("calendar sync: %d active accounts", len(accounts))
        any_failed = False
        for acc in accounts:
            try:
                stats = await sync_one_account(
                    pool, cfg, acc["email"], acc["refresh_token"]
                )
                if stats.get("scope_missing"):
                    any_failed = True
            except Exception:
                log.exception("calendar %s: top-level failure", acc["email"])
                any_failed = True
        return 1 if any_failed else 0
    finally:
        await pool.close()
