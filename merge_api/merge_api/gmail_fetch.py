"""Gmail read-side helpers for the mail client — on-demand attachment download
and lazy backfill of attachment metadata for already-ingested messages.

Like gmail_send, this is a stateless Gmail API call from merge_api reusing the
account's stored refresh token + the shared "Desktop app" client secrets. Unlike
send, it needs only the `gmail.readonly` scope, which every ingested account
already granted — so NO re-consent is required.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

import asyncpg

log = logging.getLogger(__name__)

_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
_MODIFY = "https://www.googleapis.com/auth/gmail.modify"


class GmailReadError(RuntimeError):
    """The account isn't usable for reading (no active token / auth failure)."""


class GmailModifyError(RuntimeError):
    """The account isn't authorized for gmail.modify (no token / missing scope).
    The mail client treats this as 'saved locally, not synced to Gmail' — it must
    never break the local archive/star overlay."""


class GmailGoneError(RuntimeError):
    """The message no longer exists in Gmail (404 — deleted there; our archived
    copy stays). Terminal: callers must record it so batch jobs (spam scan,
    Bayes feeder) stop re-fetching it on every run."""


def _read_app_secrets(path: str) -> dict[str, str]:
    data = json.loads(Path(path).read_text())
    inner = data.get("installed") or data.get("web") or data
    return {
        "client_id": inner["client_id"],
        "client_secret": inner["client_secret"],
        "token_uri": inner.get("token_uri", "https://oauth2.googleapis.com/token"),
    }


def _build_credentials(client_secrets_path: str, refresh_token: str,
                       scope: str = _READONLY):
    """Unrefreshed (token=None) Credentials for an account's stored refresh
    token: the first API call through them does an OAuth round-trip to
    oauth2.googleapis.com. Batch jobs must refresh ONCE per account instead
    (account_credentials) — a per-message refresh under concurrency storms
    DNS and shows up as transient 'Unable to find the server' errors."""
    from google.oauth2.credentials import Credentials

    secrets = _read_app_secrets(client_secrets_path)
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=secrets["token_uri"],
        client_id=secrets["client_id"],
        client_secret=secrets["client_secret"],
        scopes=[scope],
    )


def _service_from_creds(creds):
    """Gmail service from already-built creds. Local-only (static discovery),
    so cheap to call per message. Service objects wrap httplib2 and are NOT
    thread-safe — build one per worker thread; only the creds are shared."""
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _build_gmail_service(client_secrets_path: str, refresh_token: str,
                         scope: str = _READONLY):
    return _service_from_creds(
        _build_credentials(client_secrets_path, refresh_token, scope))


def _state_label_ops(archived: bool | None, starred: bool | None,
                     trashed: bool | None = None) -> tuple[list[str], list[str]]:
    """Map the app's archive/star/trash overlay → Gmail label add/remove lists.
    archive ⇒ remove INBOX (Gmail's archive); unarchive ⇒ add INBOX back.
    star ⇒ add STARRED; unstar ⇒ remove STARRED.
    trash ⇒ add TRASH + remove INBOX; untrash ⇒ remove TRASH + add INBOX.
    Only provided fields act; add/remove are deduped (archive+trash both
    touch INBOX) and never contradictory (trash wins on INBOX)."""
    add: set[str] = set()
    remove: set[str] = set()
    if archived is True:
        remove.add("INBOX")
    elif archived is False:
        add.add("INBOX")
    if starred is True:
        add.add("STARRED")
    elif starred is False:
        remove.add("STARRED")
    if trashed is True:
        add.add("TRASH")
        remove.add("INBOX")
        add.discard("INBOX")
    elif trashed is False:
        remove.add("TRASH")
        add.add("INBOX")
        remove.discard("INBOX")
    return sorted(add), sorted(remove)


async def _refresh_token(pool: asyncpg.Pool, account_email: str) -> str | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT refresh_token FROM raw.gmail_account WHERE email=$1 AND status='active'",
            account_email,
        )
    return row["refresh_token"] if row and row["refresh_token"] else None


async def account_credentials(pool: asyncpg.Pool, *, client_secrets_path: str,
                              account_email: str, scope: str = _READONLY):
    """One eagerly-refreshed Credentials for an account, for batch jobs that
    fetch many messages: the single refresh here replaces a per-message OAuth
    round-trip. Safe to share across worker threads once refreshed — pass as
    `creds=` to fetch_raw_message / list_spam_message_ids. Raises
    GmailReadError on a missing token or auth failure (e.g. revoked grant);
    transient errors (DNS, network) propagate raw."""
    rt = await _refresh_token(pool, account_email)
    if not rt:
        raise GmailReadError(f"no active refresh_token for {account_email}")

    def _refresh():
        from google.auth.transport.requests import Request
        creds = _build_credentials(client_secrets_path, rt, scope)
        creds.refresh(Request())
        return creds

    try:
        return await asyncio.to_thread(_refresh)
    except Exception as e:  # noqa: BLE001
        if _is_auth_error(e):
            raise GmailReadError(str(e)) from e
        raise


# --- attachment metadata parsing (mirrors fetchers/gmail/gmail/client.py) ------

def _walk_parts(payload: dict[str, Any]):
    yield payload
    for child in payload.get("parts") or []:
        yield from _walk_parts(child)


def extract_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Attachment metadata for every part with a `filename` + `body.attachmentId`.
    Same shape the fetcher stores in raw.gmail_message.payload['attachments']."""
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


# Triage-relevant raw headers (mirrors fetchers/gmail/gmail/client.py _TRIAGE_HEADERS).
_TRIAGE_HEADERS = (
    "Auto-Submitted", "Precedence", "List-Id", "List-Unsubscribe",
    "List-Unsubscribe-Post", "Authentication-Results", "Return-Path", "Reply-To",
)


def extract_headers(payload: dict[str, Any]) -> dict[str, str]:
    """The triage-relevant raw headers on a message, keyed by lowercased name —
    same shape the fetcher stores in payload['headers']."""
    headers = payload.get("headers") or []
    by_low = {h.get("name", "").lower(): h.get("value") for h in headers}
    out: dict[str, str] = {}
    for name in _TRIAGE_HEADERS:
        v = by_low.get(name.lower())
        if v is not None:
            out[name.lower()] = v
    return out


def _is_auth_error(e: BaseException) -> bool:
    msg = str(e).lower()
    return any(s in msg for s in (
        "insufficient", "invalid_grant", "invalid_scope", "insufficient_scope",
        "401", "403",
    ))


async def download_attachment(
    pool: asyncpg.Pool, *, client_secrets_path: str, account_email: str,
    message_id: str, attachment_id: str,
) -> bytes:
    """Fetch one attachment's raw bytes via attachments.get. Raises
    GmailReadError on a missing token or an auth failure."""
    rt = await _refresh_token(pool, account_email)
    if not rt:
        raise GmailReadError(f"no active refresh_token for {account_email}")

    def _get() -> bytes:
        service = _build_gmail_service(client_secrets_path, rt)
        res = service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id).execute()
        data = res.get("data") or ""
        return base64.urlsafe_b64decode(data.encode("ascii"))

    try:
        return await asyncio.to_thread(_get)
    except Exception as e:  # noqa: BLE001
        if _is_auth_error(e):
            raise GmailReadError(str(e)) from e
        raise


async def push_thread_state(
    pool: asyncpg.Pool, *, client_secrets_path: str, account_email: str,
    gmail_thread_id: str, archived: bool | None = None, starred: bool | None = None,
    trashed: bool | None = None,
) -> None:
    """Push the app's archive/star/trash change back to Gmail (Phase 3c two-way
    sync) by adding/removing INBOX/STARRED/TRASH labels on the thread. Needs the
    gmail.modify scope; raises GmailModifyError if the account isn't authorized
    (caller saves the local overlay regardless). No-op when no field changes a
    label — snooze/read are app-local and never reach Gmail."""
    add, remove = _state_label_ops(archived, starred, trashed)
    if not add and not remove:
        return
    rt = await _refresh_token(pool, account_email)
    if not rt:
        raise GmailModifyError(f"no active refresh_token for {account_email}")

    body = {"addLabelIds": add, "removeLabelIds": remove}

    def _modify() -> None:
        from googleapiclient.errors import HttpError
        service = _build_gmail_service(client_secrets_path, rt, _MODIFY)
        try:
            service.users().threads().modify(
                userId="me", id=gmail_thread_id, body=body).execute()
        except HttpError as e:
            # thread_key was a bare message_id (no Gmail thread) → modify the message.
            if getattr(e.resp, "status", None) == 404:
                service.users().messages().modify(
                    userId="me", id=gmail_thread_id, body=body).execute()
            else:
                raise

    try:
        await asyncio.to_thread(_modify)
    except Exception as e:  # noqa: BLE001
        if _is_auth_error(e):
            raise GmailModifyError(str(e)) from e
        raise


async def batch_modify(
    pool: asyncpg.Pool, *, client_secrets_path: str, account_email: str,
    message_ids: list[str], add: list[str] | None = None,
    remove: list[str] | None = None,
) -> int:
    """Bulk label change on many messages via Gmail messages.batchModify — ONE
    API call per 1000 ids (vs one modify per thread). Needs gmail.modify. Used by
    the sender cleanup actions (archive = remove INBOX, read = remove UNREAD,
    trash = add TRASH). Returns how many ids were pushed; raises GmailModifyError
    when the account can't do it (caller keeps the local overlay regardless)."""
    if not message_ids:
        return 0
    rt = await _refresh_token(pool, account_email)
    if not rt:
        raise GmailModifyError(f"no active refresh_token for {account_email}")

    def _run() -> int:
        service = _build_gmail_service(client_secrets_path, rt, _MODIFY)
        done = 0
        for i in range(0, len(message_ids), 1000):
            chunk = message_ids[i:i + 1000]
            service.users().messages().batchModify(userId="me", body={
                "ids": chunk,
                "addLabelIds": add or [],
                "removeLabelIds": remove or [],
            }).execute()
            done += len(chunk)
        return done

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:  # noqa: BLE001
        if _is_auth_error(e):
            raise GmailModifyError(str(e)) from e
        raise


async def fetch_raw_message(
    pool: asyncpg.Pool, *, client_secrets_path: str, account_email: str, message_id: str,
    creds=None,
) -> bytes:
    """The raw RFC822 bytes of a message (Gmail messages.get format=raw), for
    feeding Rspamd (Phase B2). Uses the account's existing gmail.readonly grant —
    no re-consent. Raises GmailReadError on a missing token / auth failure.
    Batch callers pass `creds=` from account_credentials() — otherwise every
    call refreshes the OAuth token."""
    if creds is None:
        rt = await _refresh_token(pool, account_email)
        if not rt:
            raise GmailReadError(f"no active refresh_token for {account_email}")

    def _get() -> bytes:
        service = (_service_from_creds(creds) if creds is not None
                   else _build_gmail_service(client_secrets_path, rt))
        res = service.users().messages().get(
            userId="me", id=message_id, format="raw").execute()
        return base64.urlsafe_b64decode((res.get("raw") or "").encode("ascii"))

    try:
        return await asyncio.to_thread(_get)
    except Exception as e:  # noqa: BLE001
        if getattr(getattr(e, "resp", None), "status", None) == 404:
            raise GmailGoneError(f"{message_id} deleted in Gmail") from e
        if _is_auth_error(e):
            raise GmailReadError(str(e)) from e
        raise


async def list_spam_message_ids(
    pool: asyncpg.Pool, *, client_secrets_path: str, account_email: str,
    limit: int = 200, creds=None,
) -> list[str]:
    """Message ids currently in the account's Gmail SPAM folder. The fetcher
    never ingests spam (archive-only excludes it), so the Bayes spam corpus is
    listed LIVE here — Gmail's own spam verdicts, refreshed by its ~30-day
    retention. Requires includeSpamTrash=True (messages.list hides SPAM/TRASH
    by default). gmail.readonly; raises GmailReadError on auth failure.
    Accepts `creds=` from account_credentials() to skip the per-call refresh."""
    if creds is None:
        rt = await _refresh_token(pool, account_email)
        if not rt:
            raise GmailReadError(f"no active refresh_token for {account_email}")

    def _list() -> list[str]:
        service = (_service_from_creds(creds) if creds is not None
                   else _build_gmail_service(client_secrets_path, rt))
        ids: list[str] = []
        page_token = None
        while len(ids) < limit:
            resp = service.users().messages().list(
                userId="me", q="in:spam", includeSpamTrash=True,
                maxResults=min(500, limit - len(ids)), pageToken=page_token,
            ).execute()
            ids.extend(m["id"] for m in resp.get("messages") or [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids[:limit]

    try:
        return await asyncio.to_thread(_list)
    except Exception as e:  # noqa: BLE001
        if _is_auth_error(e):
            raise GmailReadError(str(e)) from e
        raise


async def backfill_thread_metadata(
    pool: asyncpg.Pool, *, client_secrets_path: str, messages: list[dict[str, Any]],
) -> None:
    """For messages whose stored payload lacks `attachments` (Phase 3b) and/or
    `headers` (Phase B) — ingested before those landed — fetch
    messages.get(format='full') ONCE, extract both, persist them back into
    payload, and refresh each message's `attachments`/`signals` in place. One-time
    per message; auth/transient errors are swallowed (best-effort enrichment must
    never break the reader)."""
    from . import queries  # local import: avoid a module-load cycle

    stale = [m for m in messages
             if not m.get("_attachments_known") or not m.get("_headers_known")]
    if not stale:
        return

    # Group by account so we build one service per account, then fetch concurrently.
    by_account: dict[str, list[dict[str, Any]]] = {}
    for m in stale:
        by_account.setdefault(m["account_email"], []).append(m)

    # Cap concurrency: a thread has few messages, but the bulk header backfill
    # passes hundreds — firing them all at once trips Gmail's per-user rate limit
    # (~50 messages.get/sec). 8 in flight stays comfortably under it.
    sem = asyncio.Semaphore(8)

    for account_email, msgs in by_account.items():
        try:
            creds = await account_credentials(
                pool, client_secrets_path=client_secrets_path,
                account_email=account_email)
        except Exception as e:  # noqa: BLE001 — best-effort enrichment: skip account
            log.warning("metadata backfill: creds for %s failed: %s", account_email, e)
            continue

        def _fetch_one(message_id: str) -> dict[str, Any] | None:
            from googleapiclient.errors import HttpError
            try:
                service = _service_from_creds(creds)
                full = service.users().messages().get(
                    userId="me", id=message_id, format="full").execute()
                payload = full.get("payload") or {}
                return {"attachments": extract_attachments(payload),
                        "headers": extract_headers(payload)}
            except HttpError as e:
                # 404 = deleted in Gmail (archive-only: our copy stays). Stamp
                # empty metadata so the backlog drain stops re-fetching it on
                # every run — without this, gone messages pile up at the head of
                # the unheadered queue and eventually stall the drain entirely.
                if getattr(e.resp, "status", None) == 404:
                    log.info("metadata backfill: %s gone in Gmail — stamping empty", message_id)
                    return {"attachments": [], "headers": {}}
                log.warning("metadata backfill failed for %s: %s", message_id, e)
                return None
            except Exception as e:  # noqa: BLE001
                log.warning("metadata backfill failed for %s: %s", message_id, e)
                return None

        async def _guarded(message_id: str):
            async with sem:
                return await asyncio.to_thread(_fetch_one, message_id)

        results = await asyncio.gather(*(_guarded(m["message_id"]) for m in msgs))

        async with pool.acquire() as conn:
            for m, meta in zip(msgs, results):
                if meta is None:
                    continue
                m["attachments"] = meta["attachments"]
                m["signals"] = queries.triage_signals(meta["headers"])
                await conn.execute(
                    """
                    UPDATE raw.gmail_message
                       SET payload = jsonb_set(
                             jsonb_set(COALESCE(payload, '{}'::jsonb),
                                       '{attachments}', $3::jsonb, true),
                             '{headers}', $4::jsonb, true)
                     WHERE account_email = $1 AND message_id = $2
                    """,
                    account_email, m["message_id"],
                    json.dumps(meta["attachments"]), json.dumps(meta["headers"]),
                )
