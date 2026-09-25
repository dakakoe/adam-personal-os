"""Browser-based Google OAuth re-consent for the Sources page.

When a Gmail/GCal refresh token is revoked, the fetchers flag the account
'reauth_needed' (see fetchers/gmail). This module lets the user re-consent from
the web UI instead of running the CLI: /api/sources/oauth/start sends them to
Google's consent screen, /api/sources/oauth/callback exchanges the code and
writes the new refresh token back to raw.gmail_account.

The token exchange + profile lookup are raw httpx calls (not the sync Google
client libs) so they stay on FastAPI's async path. client_id/secret come from
the SAME GMAIL_CLIENT_SECRETS the fetchers use, so a reconnect-minted token is
automatically refreshable by the workers — but that file must be a Google
*Web application* client (Desktop clients can't use an https redirect URI).

Inert until MERGE_OAUTH_REDIRECT_URI is set: reconnect_configured() returns
False and the endpoints 503 / the UI hides the button.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
PROFILE_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

# Same shortnames as fetchers/gmail/gmail/oauth.py, kept in sync by hand.
SCOPES_BY_NAME = {
    "gmail":          "https://www.googleapis.com/auth/gmail.readonly",
    "contacts":       "https://www.googleapis.com/auth/contacts.readonly",
    "other-contacts": "https://www.googleapis.com/auth/contacts.other.readonly",
    "calendar":       "https://www.googleapis.com/auth/calendar.readonly",
    "calendar-write": "https://www.googleapis.com/auth/calendar.events",
    "gmail-send":     "https://www.googleapis.com/auth/gmail.send",
    # Two-way mail sync (Phase 3c). Superset of gmail.readonly, so an account
    # reconnected with this still serves the read-only sync + triage.
    "gmail-modify":   "https://www.googleapis.com/auth/gmail.modify",
}


def _read_web_secrets(path: str) -> dict[str, str] | None:
    """client_id/secret/token_uri from a Google client_secrets.json. Returns
    None if the file is missing/unreadable so callers can degrade to 'not
    configured' rather than crash."""
    try:
        data = json.loads(Path(path).read_text())
    except Exception as e:
        log.warning("oauth reconnect: cannot read client secrets at %s: %r", path, e)
        return None
    inner = data.get("web") or data.get("installed") or data
    if not inner.get("client_id") or not inner.get("client_secret"):
        return None
    return {
        "client_id": inner["client_id"],
        "client_secret": inner["client_secret"],
        "token_uri": inner.get("token_uri", "https://oauth2.googleapis.com/token"),
    }


def reconnect_configured(cfg) -> bool:
    """True when the web re-consent flow is fully set up: a redirect URI is
    configured AND the client secrets are readable with a client_id/secret."""
    if not getattr(cfg, "oauth_redirect_uri", None):
        return False
    return _read_web_secrets(cfg.gcal_client_secrets) is not None


def _base_scope_names(cfg) -> list[str]:
    return [s.strip() for s in (cfg.oauth_scopes or "").split(",") if s.strip()]


def scopes_for_account(cfg, email: str | None) -> list[str]:
    """Scope shortnames to request for a specific account: the base set, plus the
    write scopes only the special accounts need. This keeps a personal account
    from being over-permissioned with send/calendar-write while ensuring the work
    account doesn't LOSE those scopes when it reconnects."""
    names = list(_base_scope_names(cfg))
    el = (email or "").strip().lower()
    work = (getattr(cfg, "work_calendar_account", None) or "").lower()
    send = (getattr(cfg, "email_send_account", None) or "").lower()
    if el and el == work and "calendar-write" not in names:
        names.append("calendar-write")
    if el and el == send and "gmail-send" not in names:
        names.append("gmail-send")
    return names


def _scope_urls(names: list[str]) -> list[str]:
    urls = [SCOPES_BY_NAME[n] for n in names if n in SCOPES_BY_NAME]
    return urls or [SCOPES_BY_NAME["gmail"]]


def build_auth_url(cfg, state: str, scope_names: list[str] | None = None,
                   login_hint: str | None = None) -> str:
    """The Google consent URL. access_type=offline + prompt=consent guarantee a
    refresh_token even on re-consent of an already-authorized account.
    scope_names defaults to the base set; pass scopes_for_account() to tailor it.
    login_hint pre-selects which Google account the consent screen targets."""
    secrets = _read_web_secrets(cfg.gcal_client_secrets)
    if secrets is None:
        raise RuntimeError("oauth client secrets unreadable")
    names = scope_names if scope_names is not None else _base_scope_names(cfg)
    params = {
        "client_id": secrets["client_id"],
        "redirect_uri": cfg.oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(_scope_urls(names)),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


async def exchange_code(cfg, code: str) -> tuple[str, str, list[str]]:
    """Swap an authorization code for tokens; return (refresh_token,
    access_token, granted_scopes). The token response reports the scopes the user
    ACTUALLY granted (they can untick some on the consent screen) — persisted to
    raw.gmail_account.scopes so the UI knows per-account capabilities (can_send).
    Raises if Google didn't return a refresh_token (e.g. consent was not forced)."""
    secrets = _read_web_secrets(cfg.gcal_client_secrets)
    if secrets is None:
        raise RuntimeError("oauth client secrets unreadable")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(secrets["token_uri"], data={
            "code": code,
            "client_id": secrets["client_id"],
            "client_secret": secrets["client_secret"],
            "redirect_uri": cfg.oauth_redirect_uri,
            "grant_type": "authorization_code",
        })
    resp.raise_for_status()
    body = resp.json()
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        # No refresh_token means Google reused a prior grant; we set
        # prompt=consent precisely to avoid this, so treat it as a failure.
        raise RuntimeError("google returned no refresh_token")
    access_token = body.get("access_token")
    if not access_token:
        raise RuntimeError("google returned no access_token")
    granted = [s for s in (body.get("scope") or "").split() if s]
    return refresh_token, access_token, granted


async def fetch_account_email(access_token: str) -> str:
    """Which Google account just consented — so we write the token to the right
    raw.gmail_account row. gmail.readonly's getProfile returns emailAddress."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            PROFILE_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    resp.raise_for_status()
    email = resp.json().get("emailAddress")
    if not email:
        raise RuntimeError("gmail profile returned no emailAddress")
    return email.strip().lower()
