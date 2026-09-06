"""Gmail API client: builds a googleapiclient service from a stored
refresh_token and the app's client_id/client_secret. The Gmail API client
is synchronous; we run it in a thread executor from the async caller.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)


# 403 reasons that mean the credentials/scopes are bad and the user must
# re-consent — as opposed to a project-config 403 (accessNotConfigured) or a
# rate/quota 403, neither of which reconnecting would fix.
_REAUTH_403_REASONS = {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}


def http_error_reason(e: HttpError) -> str:
    """Machine-readable reason from a Google HttpError body, e.g.
    'accessNotConfigured', 'insufficientPermissions', 'rateLimitExceeded'."""
    try:
        body = json.loads(e.content.decode("utf-8"))
        err = body.get("error", {})
        errors = err.get("errors") or []
        if errors and errors[0].get("reason"):
            return errors[0]["reason"]
        return err.get("status", "") or ""
    except Exception:
        return ""


def is_reauth_error(e: BaseException) -> bool:
    """True when `e` means the account's OAuth credentials are no longer usable
    and the user must re-consent: a revoked/expired refresh token (RefreshError /
    invalid_grant), any 401, or a genuine scope-insufficient 403. Deliberately
    excludes accessNotConfigured (API not enabled) and rate/quota 403s, which a
    reconnect would not fix."""
    if isinstance(e, RefreshError):
        return True
    if isinstance(e, HttpError):
        status = getattr(e.resp, "status", None)
        if status == 401:
            return True
        if status == 403:
            return http_error_reason(e) in _REAUTH_403_REASONS
    return False


def _read_app_secrets(path: str) -> dict[str, str]:
    """credentials.json from Google Cloud (Desktop app type) wraps the
    real fields inside either {"installed": {...}} or {"web": {...}}."""
    data = json.loads(Path(path).read_text())
    inner = data.get("installed") or data.get("web") or data
    return {
        "client_id": inner["client_id"],
        "client_secret": inner["client_secret"],
        "token_uri": inner.get("token_uri", "https://oauth2.googleapis.com/token"),
    }


def build_service(client_secrets_path: str, refresh_token: str, scopes: list[str]):
    secrets = _read_app_secrets(client_secrets_path)
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=secrets["token_uri"],
        client_id=secrets["client_id"],
        client_secret=secrets["client_secret"],
        scopes=list(scopes),
    )
    # cache_discovery=False avoids a noisy WARNING about file cache and a
    # disk write inside the venv on every call.
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def build_calendar_service(client_secrets_path: str, refresh_token: str, scopes: list[str]):
    """Calendar API (v3) service from the same stored refresh token. Requires
    the account to have been re-consented with calendar.readonly — otherwise
    list() returns 403 'insufficient authentication scopes'."""
    secrets = _read_app_secrets(client_secrets_path)
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=secrets["token_uri"],
        client_id=secrets["client_id"],
        client_secret=secrets["client_secret"],
        scopes=list(scopes),
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _parse_event_ts(node: dict[str, Any]) -> tuple[Any, bool]:
    """A Calendar event start/end node is either {'dateTime': ISO8601(+tz)}
    for timed events or {'date': 'YYYY-MM-DD'} for all-day. Returns
    (datetime|None, all_day)."""
    from datetime import datetime, timezone
    if not node:
        return (None, False)
    dt = node.get("dateTime")
    if dt:
        try:
            return (datetime.fromisoformat(dt), False)
        except ValueError:
            return (None, False)
    d = node.get("date")
    if d:
        try:
            return (datetime.fromisoformat(d).replace(tzinfo=timezone.utc), True)
        except ValueError:
            return (None, True)
    return (None, False)


def api_event_to_row(*, account_email: str, calendar_id: str, ev: dict[str, Any]) -> dict[str, Any] | None:
    """Map a Calendar events.list (singleEvents=True) item → our row dict.
    Returns None for events with no usable start."""
    start_ts, start_all_day = _parse_event_ts(ev.get("start") or {})
    end_ts, _ = _parse_event_ts(ev.get("end") or {})
    if start_ts is None:
        return None

    attendees = ev.get("attendees") or []
    self_response = None
    for a in attendees:
        if a.get("self"):
            self_response = a.get("responseStatus")
            break
    organizer = (ev.get("organizer") or {}).get("email")

    updated_ts = None
    upd = ev.get("updated")
    if upd:
        from datetime import datetime
        try:
            updated_ts = datetime.fromisoformat(upd.replace("Z", "+00:00"))
        except ValueError:
            updated_ts = None

    return {
        "account_email": account_email,
        "calendar_id": calendar_id,
        "event_id": ev["id"],
        "summary": ev.get("summary"),
        "description": ev.get("description"),
        "location": ev.get("location"),
        "status": ev.get("status"),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "all_day": start_all_day,
        "organizer_email": organizer,
        "attendees": attendees,
        "self_response": self_response,
        "html_link": ev.get("htmlLink"),
        "recurring_event_id": ev.get("recurringEventId"),
        "updated_ts": updated_ts,
        "payload": {"hangoutLink": ev.get("hangoutLink"), "etag": ev.get("etag")},
    }


_HEADER_PAIRS = ("From", "To", "Cc", "Bcc", "Subject", "Message-ID", "Date")

# Raw headers kept for mail auto-triage (Phase B). The From/Subject/etc. above are
# already first-class columns; these are the bulk/automation/auth signals the
# triage heuristics read. Stored under payload['headers'] with lowercased keys.
_TRIAGE_HEADERS = (
    "Auto-Submitted", "Precedence", "List-Id", "List-Unsubscribe",
    "List-Unsubscribe-Post", "Authentication-Results", "Return-Path", "Reply-To",
)


def _hget(headers: list[dict[str, str]], name: str) -> str | None:
    name_low = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_low:
            return h.get("value")
    return None


def _extract_headers(headers: list[dict[str, str]]) -> dict[str, str]:
    """The triage-relevant raw headers present on a message, keyed by lowercased
    name (so the SQL heuristics can look them up case-insensitively). Absent
    headers are simply omitted."""
    out: dict[str, str] = {}
    for name in _TRIAGE_HEADERS:
        v = _hget(headers, name)
        if v is not None:
            out[name.lower()] = v
    return out


def _walk_parts(payload: dict[str, Any]):
    """Depth-first iter of all parts. Each `part` has `mimeType` + body."""
    yield payload
    for child in payload.get("parts") or []:
        yield from _walk_parts(child)


def _decode_body(part: dict[str, Any]) -> str | None:
    body = part.get("body") or {}
    data = body.get("data")
    if not data:
        return None
    try:
        raw = base64.urlsafe_b64decode(data.encode("ascii"))
    except Exception:
        return None
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def _extract_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Attachment metadata for every part that carries a real attachment — i.e.
    a `filename` plus a `body.attachmentId` (the handle the on-demand
    attachments.get endpoint needs). Inline images without a filename are
    skipped. The bytes are NOT fetched here; only the metadata, which is already
    in the format='full' response we parse, so this costs no extra API call."""
    out: list[dict[str, Any]] = []
    for part in _walk_parts(payload):
        filename = (part.get("filename") or "").strip()
        body = part.get("body") or {}
        attachment_id = body.get("attachmentId")
        if not filename or not attachment_id:
            continue
        out.append({
            "filename": filename,
            "mime_type": part.get("mimeType") or "application/octet-stream",
            "size": body.get("size"),
            "attachment_id": attachment_id,
        })
    return out


def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    text = html = None
    for part in _walk_parts(payload):
        mt = part.get("mimeType", "")
        if mt == "text/plain" and text is None:
            text = _decode_body(part)
        elif mt == "text/html" and html is None:
            html = _decode_body(part)
        if text and html:
            break
    return text, html


def _parse_address(value: str | None) -> tuple[str | None, str | None]:
    import email.utils
    if not value:
        return (None, None)
    name, addr = email.utils.parseaddr(value)
    addr = (addr or "").strip().lower() or None
    name = (name or "").strip() or None
    return (name, addr)


def _split_addr_list(value: str | None) -> list[str]:
    import email.utils
    if not value:
        return []
    return [a.strip().lower() for _, a in email.utils.getaddresses([value]) if a and "@" in a]


def api_message_to_row(*, account_email: str, msg: dict[str, Any]) -> dict[str, Any] | None:
    """Map Gmail API messages.get(format='full') response → our row dict."""
    payload = msg.get("payload") or {}
    headers = payload.get("headers") or []
    from_name, from_addr = _parse_address(_hget(headers, "From"))
    subject = _hget(headers, "Subject")
    message_id_hdr = _hget(headers, "Message-ID")

    text, html = _extract_bodies(payload)
    internal_date_ms = msg.get("internalDate")
    if not internal_date_ms:
        return None
    from datetime import datetime, timezone
    internal_date = datetime.fromtimestamp(int(internal_date_ms) / 1000, tz=timezone.utc)

    rfc822 = None
    if message_id_hdr:
        rfc822 = message_id_hdr.strip().strip("<>").strip() or None

    return {
        "account_email": account_email,
        "message_id": msg["id"],
        "thread_id": msg.get("threadId"),
        "rfc822_message_id": rfc822,
        "from_address": from_addr,
        "from_name": from_name,
        "to_addresses": _split_addr_list(_hget(headers, "To")),
        "cc_addresses": _split_addr_list(_hget(headers, "Cc")),
        "bcc_addresses": _split_addr_list(_hget(headers, "Bcc")),
        "subject": subject,
        "body_text": text,
        "body_html": html,
        "internal_date": internal_date,
        "labels": list(msg.get("labelIds") or []),
        "source": "live",
        "payload": {
            "snippet": msg.get("snippet"),
            "attachments": _extract_attachments(payload),
            "headers": _extract_headers(headers),
        },
    }
