"""Google Calendar WRITE — create events on a work calendar from a confirmed
bot capture. The read sync lives in the gmail fetcher; merge_api can't import
that package, so the tiny service-builder is duplicated here.

Requires the target work account to have been re-consented with the
`calendar.events` scope (see fetchers/gmail/gmail/oauth.py `calendar-write`).
Until then `events().insert` returns 403 and the caller leaves the capture
pending so a retry works after re-consent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncpg

log = logging.getLogger(__name__)

DEFAULT_TZ = "Asia/Bangkok"
DEFAULT_DURATION_MIN = 60


class CalendarAuthError(RuntimeError):
    """The work account isn't authorized for calendar writes (no token / 403)."""


def eligible_invite_emails(participants: list[dict]) -> list[str]:
    """Which participants may actually be emailed a Google Calendar invite.

    FAIL-CLOSED on privacy: a participant flagged `sensitive` is NEVER put on an
    external invite (same policy as sensitive-contact routing) — they can still
    be tracked internally, just not exposed to Google. A participant with no
    resolvable email is silently skipped (nothing to invite). Deduped
    case-insensitively, order preserved. Each participant is a dict with at
    least {email, sensitive}."""
    out: list[str] = []
    seen: set[str] = set()
    for p in participants:
        if p.get("sensitive"):
            continue
        email = (p.get("email") or "").strip()
        if not email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(email)
    return out


def _read_app_secrets(path: str) -> dict[str, str]:
    data = json.loads(Path(path).read_text())
    inner = data.get("installed") or data.get("web") or data
    return {
        "client_id": inner["client_id"],
        "client_secret": inner["client_secret"],
        "token_uri": inner.get("token_uri", "https://oauth2.googleapis.com/token"),
    }


def _build_calendar_service(client_secrets_path: str, refresh_token: str,
                            scopes: list[str] | None = None):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    secrets = _read_app_secrets(client_secrets_path)
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=secrets["token_uri"],
        client_id=secrets["client_id"],
        client_secret=secrets["client_secret"],
        scopes=scopes or ["https://www.googleapis.com/auth/calendar.events"],
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _list_calendars_sync(service) -> list[dict]:
    items, token = [], None
    while True:
        page = service.calendarList().list(pageToken=token, maxResults=100).execute()
        items.extend(page.get("items") or [])
        token = page.get("nextPageToken")
        if not token:
            return items


async def list_account_calendars(
    pool: asyncpg.Pool, *, client_secrets_path: str, account_email: str,
) -> list[dict]:
    """Writable calendars on the account (the sync superset already grants
    calendar.readonly — no extra consent). [{id, summary, primary}]."""
    rt = await _work_refresh_token(pool, account_email)
    if not rt:
        raise CalendarAuthError(f"no active refresh_token for {account_email}")
    service = _build_calendar_service(
        client_secrets_path, rt,
        scopes=["https://www.googleapis.com/auth/calendar.readonly"])
    try:
        items = await asyncio.to_thread(_list_calendars_sync, service)
    except Exception as e:  # noqa: BLE001
        if any(s in str(e).lower() for s in _AUTH_SIGNALS):
            raise CalendarAuthError(str(e)) from e
        raise
    out = [{"id": c.get("id"), "summary": c.get("summaryOverride") or c.get("summary") or c.get("id"),
            "primary": bool(c.get("primary"))}
           for c in items if c.get("accessRole") in ("owner", "writer")]
    # primary first, then alphabetical
    out.sort(key=lambda c: (not c["primary"], (c["summary"] or "").lower()))
    return out


async def _work_refresh_token(pool: asyncpg.Pool, account_email: str) -> str | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT refresh_token FROM raw.gmail_account WHERE email=$1 AND status='active'",
            account_email,
        )
    return row["refresh_token"] if row and row["refresh_token"] else None


def _apply_tz(iso: str, tz: ZoneInfo) -> datetime:
    """Take the wall-clock components of an ISO datetime and localize them to
    the event's timezone. We DROP any offset the model emitted — LLMs are
    unreliable at tz math, so the (tz name, wall-clock) pair is authoritative:
    '12:00' + Asia/Seoul → 12:00+09:00, never a converted hour."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.replace(tzinfo=tz)


def _insert_sync(service, body: dict, send_updates: str, conference: bool = False,
                 calendar_id: str = "primary") -> dict:
    return service.events().insert(
        calendarId=calendar_id, body=body, sendUpdates=send_updates,
        conferenceDataVersion=1 if conference else 0,
    ).execute()


async def insert_event(
    pool: asyncpg.Pool, *, client_secrets_path: str, account_email: str,
    summary: str, description: str | None, start: str, end: str | None,
    all_day: bool, location: str | None, tz: str | None = None,
    attendee_email: str | None = None, conference: bool = False,
) -> dict:
    """Create an event on the work account's primary calendar. Returns
    {event_id, html_link, hangout_link}. When conference=True, attaches a Google
    Meet link (hangout_link). Raises CalendarAuthError if not authorized."""
    rt = await _work_refresh_token(pool, account_email)
    if not rt:
        raise CalendarAuthError(f"no active refresh_token for {account_email}")

    tz_name = tz or DEFAULT_TZ
    try:
        zone = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 — unknown tz from the model → fall back
        tz_name, zone = DEFAULT_TZ, ZoneInfo(DEFAULT_TZ)

    if all_day:
        start_date = (start or "")[:10]
        end_date = (end or "")[:10]
        if not end_date or end_date <= start_date:
            # Google all-day end.date is EXCLUSIVE → +1 day for a single-day event.
            d = datetime.fromisoformat(start_date)
            end_date = (d + timedelta(days=1)).date().isoformat()
        body = {"start": {"date": start_date}, "end": {"date": end_date}}
    else:
        start_dt = _apply_tz(start, zone)
        end_dt = _apply_tz(end, zone) if end else start_dt + timedelta(minutes=DEFAULT_DURATION_MIN)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=DEFAULT_DURATION_MIN)
        body = {
            "start": {"dateTime": start_dt.isoformat(), "timeZone": tz_name},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": tz_name},
        }
    body["summary"] = summary
    if description:
        body["description"] = description
    # A conference (Google Meet) and a physical location are mutually exclusive
    # in this flow: an online meeting gets a Meet link, an in-person one a place.
    if conference:
        body["conferenceData"] = {"createRequest": {
            "requestId": uuid.uuid4().hex,
            "conferenceSolutionKey": {"type": "hangoutsMeet"},
        }}
    elif location:
        body["location"] = location
    send_updates = "none"
    if attendee_email:
        body["attendees"] = [{"email": attendee_email}]
        send_updates = "all"   # Google emails the invite to the attendee

    try:
        service = _build_calendar_service(client_secrets_path, rt)
        ev = await asyncio.to_thread(_insert_sync, service, body, send_updates, conference)
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        # Not-yet-authorized signals: token refresh rejects the write scope
        # (invalid_scope/invalid_grant) or the API returns 403/insufficient.
        if any(s in msg for s in (
            "insufficient", "403", "accessnotconfigured", "invalid_scope",
            "invalid_grant", "insufficient_scope",
        )):
            raise CalendarAuthError(str(e)) from e
        raise
    return {"event_id": ev.get("id"), "html_link": ev.get("htmlLink"),
            "hangout_link": ev.get("hangoutLink")}


# --- task → calendar sync (one-way) ---------------------------------------

_AUTH_SIGNALS = ("insufficient", "403", "accessnotconfigured", "invalid_scope",
                 "invalid_grant", "insufficient_scope")
_GONE_SIGNALS = ("404", "notfound", "not found", "410", "deleted", "has been deleted")


def _update_sync(service, event_id: str, body: dict, calendar_id: str = "primary",
                 send_updates: str = "none") -> dict:
    return service.events().update(
        calendarId=calendar_id, eventId=event_id, body=body, sendUpdates=send_updates,
    ).execute()


def _delete_sync(service, event_id: str, calendar_id: str = "primary",
                 send_updates: str = "none") -> None:
    service.events().delete(
        calendarId=calendar_id, eventId=event_id, sendUpdates=send_updates,
    ).execute()


def _patch_sync(service, event_id: str, body: dict, calendar_id: str = "primary") -> dict:
    # events.patch merges: attendees + an attached Meet link survive untouched
    # fields. sendUpdates="all" → an invited attendee is emailed the change.
    return service.events().patch(
        calendarId=calendar_id, eventId=event_id, body=body, sendUpdates="all",
    ).execute()


async def update_event(
    pool: asyncpg.Pool, *, client_secrets_path: str, account_email: str,
    event_id: str, summary: str, description: str | None, start: str,
    end: str | None, all_day: bool, location: str | None, tz: str | None = None,
) -> dict:
    """Patch an existing event (the bot's post-create ✏️ Edit). Explicit nulls
    flip date↔dateTime cleanly when toggling all-day."""
    rt = await _work_refresh_token(pool, account_email)
    if not rt:
        raise CalendarAuthError(f"no active refresh_token for {account_email}")

    tz_name = tz or DEFAULT_TZ
    try:
        zone = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz_name, zone = DEFAULT_TZ, ZoneInfo(DEFAULT_TZ)

    if all_day:
        start_date = (start or "")[:10]
        end_date = (end or "")[:10]
        if not end_date or end_date <= start_date:
            d = datetime.fromisoformat(start_date)
            end_date = (d + timedelta(days=1)).date().isoformat()
        body = {"start": {"date": start_date, "dateTime": None},
                "end": {"date": end_date, "dateTime": None}}
    else:
        start_dt = _apply_tz(start, zone)
        end_dt = _apply_tz(end, zone) if end else start_dt + timedelta(minutes=DEFAULT_DURATION_MIN)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=DEFAULT_DURATION_MIN)
        body = {"start": {"dateTime": start_dt.isoformat(), "timeZone": tz_name, "date": None},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": tz_name, "date": None}}
    body["summary"] = summary
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location

    try:
        service = _build_calendar_service(client_secrets_path, rt)
        ev = await asyncio.to_thread(_patch_sync, service, event_id, body)
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if any(s in msg for s in _AUTH_SIGNALS):
            raise CalendarAuthError(str(e)) from e
        raise
    return {"event_id": ev.get("id"), "html_link": ev.get("htmlLink"),
            "hangout_link": ev.get("hangoutLink")}


def _task_event_body(summary, description, start, end, all_day, tz_name, zone,
                     duration_min: int | None = None) -> dict:
    """Build a calendar event body from a task's date/time (same all-day vs timed
    rules as insert_event; no attendees/conference). duration_min sets the timed
    event length when no explicit end is given (default 60)."""
    dur = duration_min if (duration_min and duration_min > 0) else DEFAULT_DURATION_MIN
    if all_day:
        start_date = (start or "")[:10]
        end_date = (end or "")[:10]
        if not end_date or end_date <= start_date:
            d = datetime.fromisoformat(start_date)
            end_date = (d + timedelta(days=1)).date().isoformat()
        body: dict = {"start": {"date": start_date}, "end": {"date": end_date}}
    else:
        start_dt = _apply_tz(start, zone)
        end_dt = _apply_tz(end, zone) if end else start_dt + timedelta(minutes=dur)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=dur)
        body = {"start": {"dateTime": start_dt.isoformat(), "timeZone": tz_name},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": tz_name}}
    body["summary"] = summary or "(task)"
    if description:
        body["description"] = description
    return body


async def upsert_task_event(
    pool: asyncpg.Pool, *, client_secrets_path: str, account_email: str,
    event_id: str | None, summary: str, description: str | None,
    start: str, end: str | None, all_day: bool, tz: str | None = None,
    duration_min: int | None = None, calendar_id: str = "primary",
) -> dict:
    """Create (event_id None) or update an event mirroring a task on the
    account's chosen calendar (default primary). If the stored event was
    deleted in Google (404/410), transparently recreate it."""
    rt = await _work_refresh_token(pool, account_email)
    if not rt:
        raise CalendarAuthError(f"no active refresh_token for {account_email}")
    tz_name = tz or DEFAULT_TZ
    try:
        zone = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz_name, zone = DEFAULT_TZ, ZoneInfo(DEFAULT_TZ)
    body = _task_event_body(summary, description, start, end, all_day, tz_name, zone, duration_min)
    service = _build_calendar_service(client_secrets_path, rt)
    cal = calendar_id or "primary"
    try:
        if event_id:
            ev = await asyncio.to_thread(_update_sync, service, event_id, body, cal)
        else:
            ev = await asyncio.to_thread(_insert_sync, service, body, "none", False, cal)
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if any(s in msg for s in _AUTH_SIGNALS):
            raise CalendarAuthError(str(e)) from e
        if event_id and any(s in msg for s in _GONE_SIGNALS):
            ev = await asyncio.to_thread(_insert_sync, service, body, "none", False, cal)  # recreate
        else:
            raise
    return {"event_id": ev.get("id"), "html_link": ev.get("htmlLink")}


async def delete_task_event(
    pool: asyncpg.Pool, *, client_secrets_path: str, account_email: str, event_id: str,
    calendar_id: str = "primary", notify: bool = False,
) -> None:
    """Remove a task's calendar event (or a routine's recurring series — delete
    by id removes the whole series). Already-gone (404/410) is a no-op.
    notify=True emails a cancellation to any invited attendees (used when a
    routine that had participants is unsynced/deleted)."""
    rt = await _work_refresh_token(pool, account_email)
    if not rt:
        return
    service = _build_calendar_service(client_secrets_path, rt)
    send = "all" if notify else "none"
    try:
        await asyncio.to_thread(_delete_sync, service, event_id, calendar_id or "primary", send)
    except Exception as e:  # noqa: BLE001
        if any(s in str(e).lower() for s in _GONE_SIGNALS):
            return
        raise


async def upsert_recurring_event(
    pool: asyncpg.Pool, *, client_secrets_path: str, account_email: str,
    event_id: str | None, summary: str, description: str | None,
    start: str, end: str | None, all_day: bool, rrule: str, tz: str | None = None,
    duration_min: int | None = None, calendar_id: str = "primary",
    attendee_emails: list[str] | None = None,
) -> dict:
    """Create/update a single RECURRING event (one event with an RRULE) mirroring
    a routine, on the account's chosen calendar. Recreates on 404/410.

    attendee_emails puts participants on the series: Google then emails ONE invite
    covering every occurrence (and, on an update, notifies them of the change).
    Pass only INVITE-eligible emails (see eligible_invite_emails) — this function
    does not re-filter for sensitivity. events.update REPLACES the attendee list,
    so passing [] on a previously-invited series correctly un-invites everyone."""
    rt = await _work_refresh_token(pool, account_email)
    if not rt:
        raise CalendarAuthError(f"no active refresh_token for {account_email}")
    tz_name = tz or DEFAULT_TZ
    try:
        zone = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz_name, zone = DEFAULT_TZ, ZoneInfo(DEFAULT_TZ)
    body = _task_event_body(summary, description, start, end, all_day, tz_name, zone, duration_min)
    body["recurrence"] = [rrule]
    emails = attendee_emails or []
    # Always set the key (even to []) so update() clears removed attendees.
    body["attendees"] = [{"email": e} for e in emails]
    send = "all" if emails else "none"   # only ping people when there ARE people
    service = _build_calendar_service(client_secrets_path, rt)
    cal = calendar_id or "primary"
    try:
        if event_id:
            ev = await asyncio.to_thread(_update_sync, service, event_id, body, cal, send)
        else:
            ev = await asyncio.to_thread(_insert_sync, service, body, send, False, cal)
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if any(s in msg for s in _AUTH_SIGNALS):
            raise CalendarAuthError(str(e)) from e
        if event_id and any(s in msg for s in _GONE_SIGNALS):
            ev = await asyncio.to_thread(_insert_sync, service, body, send, False, cal)
        else:
            raise
    return {"event_id": ev.get("id"), "html_link": ev.get("htmlLink")}
