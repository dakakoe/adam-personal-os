"""Gmail send — deliver a reviewed email draft via the Gmail API. Unlike the
Telegram path (which needs the held Telethon session), this is a stateless API
call, so the merge API sends directly. Reuses the same Google "Desktop app"
client secrets + the send account's stored refresh token.

The send account must be re-consented with the `gmail.send` scope
(fetchers/gmail/gmail/oauth.py `gmail-send`) — alongside its existing scopes.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)


class GmailAuthError(RuntimeError):
    """The send account isn't authorized for gmail.send (no token / 403)."""


def _read_app_secrets(path: str) -> dict[str, str]:
    data = json.loads(Path(path).read_text())
    inner = data.get("installed") or data.get("web") or data
    return {
        "client_id": inner["client_id"],
        "client_secret": inner["client_secret"],
        "token_uri": inner.get("token_uri", "https://oauth2.googleapis.com/token"),
    }


def _build_gmail_service(client_secrets_path: str, refresh_token: str):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    secrets = _read_app_secrets(client_secrets_path)
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=secrets["token_uri"],
        client_id=secrets["client_id"],
        client_secret=secrets["client_secret"],
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


async def _refresh_token(pool: asyncpg.Pool, account_email: str) -> str | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT refresh_token FROM raw.gmail_account WHERE email=$1 AND status='active'",
            account_email,
        )
    return row["refresh_token"] if row and row["refresh_token"] else None


def _html_to_text(html: str) -> str:
    """Cheap plain-text alternative from compose HTML (br/blocks → newlines, tags
    stripped) so the multipart carries a readable text/plain part for clients that
    prefer it. Not a full renderer — the compose HTML is simple + our own."""
    t = re.sub(r"(?i)<br\s*/?>", "\n", html)
    t = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
           .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _send_sync(service, *, to_email: str, from_email: str, subject: str | None, body: str,
               html: str | None = None,
               in_reply_to: str | None = None, references: str | None = None,
               thread_id: str | None = None) -> dict:
    if html:
        # multipart/alternative: text/plain fallback + text/html (P5 rich compose).
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body or _html_to_text(html), "plain", _charset="utf-8"))
        msg.attach(MIMEText(html, "html", _charset="utf-8"))
    else:
        msg = MIMEText(body, _charset="utf-8")
    msg["To"] = to_email
    msg["From"] = from_email
    msg["Subject"] = subject or "(no subject)"
    # reply threading: standard headers so the recipient's client threads it, plus
    # Gmail's threadId so it lands in the same conversation in the sender's mailbox.
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    send_body: dict = {"raw": raw}
    if thread_id:
        send_body["threadId"] = thread_id
    return service.users().messages().send(userId="me", body=send_body).execute()


async def send_email(
    pool: asyncpg.Pool, *, client_secrets_path: str, account_email: str,
    to_email: str, subject: str | None, body: str, html: str | None = None,
    in_reply_to: str | None = None, references: str | None = None,
    thread_id: str | None = None,
) -> dict:
    """Send an email from account_email to to_email. Returns {message_id}.
    `html` (optional) sends multipart/alternative with `body` as the text
    fallback (derived from html if empty). Pass in_reply_to/references/thread_id
    to thread a reply. Raises GmailAuthError if not authorized for gmail.send."""
    rt = await _refresh_token(pool, account_email)
    if not rt:
        raise GmailAuthError(f"no active refresh_token for {account_email}")
    try:
        service = _build_gmail_service(client_secrets_path, rt)
        res = await asyncio.to_thread(
            _send_sync, service, to_email=to_email, from_email=account_email,
            subject=subject, body=body, html=html, in_reply_to=in_reply_to,
            references=references, thread_id=thread_id,
        )
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if any(s in msg for s in (
            "insufficient", "403", "invalid_scope", "invalid_grant", "insufficient_scope",
        )):
            raise GmailAuthError(str(e)) from e
        raise
    return {"message_id": res.get("id")}
