"""Google Contacts (People API) sync.

For each authed account, walk people/me/connections, store full contact
records in raw.google_contact. A separate normalizer pass folds them into
canonical.identity + canonical.person.

Requires the contacts.readonly scope to be on the account's refresh_token.
If not, the People API call returns 403 / "Request had insufficient
authentication scopes" — the fix is to re-run oauth.py with --scopes
including 'contacts'.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import asyncpg
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import client as gclient
from .config import Config

log = logging.getLogger(__name__)

_PERSON_FIELDS = (
    "names,emailAddresses,phoneNumbers,organizations,biographies,"
    "birthdays,memberships,metadata,photos"
)

# otherContacts.list supports a narrower readMask than people.connections.list.
# Phones / orgs / biographies are unavailable here — these are auto-collected
# entries, not user-curated, so they're identifier-only.
_OTHER_CONTACTS_READ_MASK = "names,emailAddresses,metadata,photos"


def _build_people_service(client_secrets_path: str, refresh_token: str, scopes: list[str]):
    secrets = gclient._read_app_secrets(client_secrets_path)
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=secrets["token_uri"],
        client_id=secrets["client_id"],
        client_secret=secrets["client_secret"],
        scopes=list(scopes),
    )
    return build("people", "v1", credentials=creds, cache_discovery=False)


def _list_connections(service) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page_token = None
    while True:
        resp = service.people().connections().list(
            resourceName="people/me",
            pageSize=1000,
            personFields=_PERSON_FIELDS,
            pageToken=page_token,
        ).execute()
        out.extend(resp.get("connections") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            return out


def _list_other_contacts(service) -> list[dict[str, Any]]:
    """The auto-collected directory Gmail builds from outgoing recipients.
    Often 10-100x larger than saved Contacts for personal Gmail users."""
    out: list[dict[str, Any]] = []
    page_token = None
    while True:
        resp = service.otherContacts().list(
            pageSize=1000,
            readMask=_OTHER_CONTACTS_READ_MASK,
            pageToken=page_token,
        ).execute()
        out.extend(resp.get("otherContacts") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            return out


def _first(items: list[dict[str, Any]] | None, key: str) -> str | None:
    if not items:
        return None
    for item in items:
        v = item.get(key)
        if v:
            return str(v).strip() or None
    return None


def _all(items: list[dict[str, Any]] | None, key: str) -> list[str]:
    if not items:
        return []
    out: list[str] = []
    for item in items:
        v = item.get(key)
        if v:
            s = str(v).strip()
            if s and s not in out:
                out.append(s)
    return out


def _connection_to_row(account_email: str, c: dict[str, Any]) -> dict[str, Any]:
    metadata = c.get("metadata") or {}
    resource_name = c.get("resourceName") or ""
    etag = c.get("etag")

    names = c.get("names") or []
    display_name = _first(names, "displayName")
    given = _first(names, "givenName")
    family = _first(names, "familyName")

    emails_raw = c.get("emailAddresses") or []
    emails = sorted({e["value"].strip().lower() for e in emails_raw
                     if e.get("value") and "@" in e["value"]})

    phones_raw = c.get("phoneNumbers") or []
    phones = sorted({
        (p.get("canonicalForm") or p.get("value") or "").strip()
        for p in phones_raw
    } - {""})

    orgs = c.get("organizations") or []
    organization = _first(orgs, "name")
    job_title = _first(orgs, "title")

    notes_list = c.get("biographies") or []
    notes = _first(notes_list, "value")

    # Birthday: People API returns either a `date` object {year?, month, day}
    # or a free-text fallback. Year is often missing for privacy — we still
    # store with a sentinel year (1900) so MM-DD reminders work.
    birthday = None
    for b in c.get("birthdays") or []:
        d = b.get("date") or {}
        m = d.get("month")
        day = d.get("day")
        if m and day:
            y = d.get("year") or 1900
            try:
                from datetime import date
                birthday = date(int(y), int(m), int(day))
                break
            except ValueError:
                continue

    return {
        "account_email": account_email,
        "resource_name": resource_name,
        "etag": etag,
        "display_name": display_name,
        "given_name": given,
        "family_name": family,
        "emails": emails,
        "phones": phones,
        "organization": organization,
        "job_title": job_title,
        "notes": notes,
        "birthday": birthday,
        "payload": c,
    }


async def _upsert_contacts(pool: asyncpg.Pool, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO raw.google_contact
              (account_email, resource_name, etag, display_name, given_name, family_name,
               emails, phones, organization, job_title, notes, birthday, payload)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)
            ON CONFLICT (account_email, resource_name) DO UPDATE SET
              etag         = EXCLUDED.etag,
              display_name = EXCLUDED.display_name,
              given_name   = EXCLUDED.given_name,
              family_name  = EXCLUDED.family_name,
              emails       = EXCLUDED.emails,
              phones       = EXCLUDED.phones,
              organization = EXCLUDED.organization,
              job_title    = EXCLUDED.job_title,
              notes        = EXCLUDED.notes,
              birthday     = EXCLUDED.birthday,
              payload      = EXCLUDED.payload,
              ingested_at  = now()
            """,
            [(
                r["account_email"], r["resource_name"], r["etag"],
                r["display_name"], r["given_name"], r["family_name"],
                r["emails"], r["phones"], r["organization"], r["job_title"],
                r["notes"], r.get("birthday"),
                json.dumps(r["payload"], default=str),
            ) for r in rows],
        )
    return len(rows)


async def sync_one_account(
    pool: asyncpg.Pool, cfg: Config, account_email: str, refresh_token: str,
) -> dict[str, int]:
    service = _build_people_service(
        cfg.client_secrets_path, refresh_token, list(cfg.scopes)
    )

    saved_total = 0
    fetched_saved = 0
    fetched_other = 0
    scope_missing = 0

    # 1. Saved contacts (people/me/connections) — the user's curated list.
    try:
        conns = await asyncio.to_thread(_list_connections, service)
        rows = [_connection_to_row(account_email, c) for c in conns]
        saved_total += await _upsert_contacts(pool, rows)
        fetched_saved = len(rows)
    except HttpError as e:
        if e.resp.status in (401, 403):
            log.warning(
                "contacts %s saved-list: %d %s — re-auth with contacts.readonly",
                account_email, e.resp.status,
                e._get_reason() if hasattr(e, "_get_reason") else "",
            )
            scope_missing = 1
        else:
            raise

    # 2. Other contacts (otherContacts.list) — auto-collected directory. This
    # is where the bulk of useful identifiers live for most personal Gmail.
    try:
        others = await asyncio.to_thread(_list_other_contacts, service)
        # Same row shape; other-contacts have a different resourceName prefix
        # ("otherContacts/..."), so the (account_email, resource_name) unique
        # constraint won't collide with saved contacts.
        rows = [_connection_to_row(account_email, c) for c in others]
        saved_total += await _upsert_contacts(pool, rows)
        fetched_other = len(rows)
    except HttpError as e:
        if e.resp.status in (401, 403):
            log.warning(
                "contacts %s other-list: %d %s — re-auth with contacts.other.readonly",
                account_email, e.resp.status,
                e._get_reason() if hasattr(e, "_get_reason") else "",
            )
            scope_missing = 1
        else:
            raise

    log.info(
        "contacts %s: saved=%d other=%d total_saved=%d",
        account_email, fetched_saved, fetched_other, saved_total,
    )
    return {
        "fetched_saved": fetched_saved,
        "fetched_other": fetched_other,
        "saved": saved_total,
        "scope_missing": scope_missing,
    }


async def run(cfg: Config) -> int:
    from . import db as gdb
    pool = await gdb.connect(cfg.db_url)
    try:
        accounts = await gdb.list_active_accounts(pool)
        log.info("contacts sync: %d active accounts", len(accounts))
        any_failed = False
        for acc in accounts:
            try:
                stats = await sync_one_account(
                    pool, cfg, acc["email"], acc["refresh_token"]
                )
                log.info("contacts %s done: %s", acc["email"], stats)
                if stats.get("scope_missing"):
                    any_failed = True
            except Exception:
                log.exception("contacts %s: top-level failure", acc["email"])
                any_failed = True
        return 1 if any_failed else 0
    finally:
        await pool.close()
