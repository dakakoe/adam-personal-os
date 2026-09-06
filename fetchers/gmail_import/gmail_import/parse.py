"""mbox parsing helpers.

We deliberately use email.policy.default (the modern message API) so
get_body()/get_content() handle MIME decoding for us instead of us
hand-rolling charset detection. The legacy mailbox.mbox class returns
old-style Message objects unless we reconstruct via the policy-aware
parser, which is what _to_modern() does.
"""

from __future__ import annotations

import email
import email.message
import email.policy
import email.utils
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


def _to_modern(raw_msg: Any) -> email.message.EmailMessage:
    """Parse the raw mbox bytes through email.policy.default so we get the
    EmailMessage interface (get_body, get_content, iter_attachments)."""
    if isinstance(raw_msg, email.message.EmailMessage):
        return raw_msg
    raw_bytes = raw_msg.as_bytes()
    return email.message_from_bytes(raw_bytes, policy=email.policy.default)


def parse_address(value: str | None) -> tuple[str | None, str | None]:
    """('Jane Doe <jane@x>') -> ('Jane Doe', 'jane@x'). Returns (None,None) for
    empty/unparseable input. Email is lowercased for downstream dedup."""
    if not value:
        return (None, None)
    name, addr = email.utils.parseaddr(str(value))
    addr = addr.strip().lower() if addr else None
    name = name.strip() or None
    if addr and "@" not in addr:
        addr = None  # parseaddr can return garbage on broken headers
    return (name, addr)


def parse_address_list(value: str | None) -> list[tuple[str | None, str | None]]:
    """Multi-address header → list of (name, addr) pairs."""
    if not value:
        return []
    out = []
    for n, a in email.utils.getaddresses([str(value)]):
        a = (a or "").strip().lower()
        if a and "@" in a:
            out.append(((n or None), a))
    return out


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Some senders ship naive dates; assume UTC. Better than dropping.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_labels(raw_msg: Any) -> list[str]:
    """Gmail Takeout writes X-Gmail-Labels: a,b,c on every message."""
    raw = raw_msg.get("X-Gmail-Labels") or ""
    if not raw:
        return []
    return [s.strip() for s in re.split(r",\s*", str(raw)) if s.strip()]


def extract_bodies(msg: email.message.EmailMessage) -> tuple[str | None, str | None]:
    """Return (text, html). Use the modern API which handles decoding for us;
    skip silently on broken messages instead of trapping the run."""
    text = html = None
    try:
        body = msg.get_body(preferencelist=("plain",))
        if body is not None:
            text = body.get_content().strip() or None
    except (LookupError, KeyError, ValueError, UnicodeDecodeError, AssertionError, TypeError):
        text = None
    try:
        htmlbody = msg.get_body(preferencelist=("html",))
        if htmlbody is not None:
            html = htmlbody.get_content().strip() or None
    except (LookupError, KeyError, ValueError, UnicodeDecodeError, AssertionError, TypeError):
        html = None
    return text, html


def rfc822_message_id(msg: email.message.EmailMessage) -> str | None:
    """Canonical message identifier across exports. Falls back to hashing a
    few stable headers when Message-ID is missing (rare but happens)."""
    mid = msg.get("Message-ID") or msg.get("Message-Id") or msg.get("message-id")
    if mid:
        mid = str(mid).strip().strip("<>").strip()
        if mid:
            return mid
    # Synthesize a stable id from date + from + subject so re-imports of the
    # same export still dedup.
    parts = [
        str(msg.get("Date") or ""),
        str(msg.get("From") or ""),
        str(msg.get("Subject") or ""),
    ]
    return "synthetic-" + hashlib.sha1("|".join(parts).encode("utf-8", "ignore")).hexdigest()


def message_to_row(
    raw_msg: Any, *, account_email: str
) -> dict[str, Any] | None:
    """Convert one mbox message into the dict we insert into raw.gmail_message.
    Returns None if the message is so malformed we can't extract a date."""
    try:
        msg = _to_modern(raw_msg)
    except Exception:
        log.exception("mbox: failed to re-parse a message; skipping")
        return None

    internal_date = parse_date(msg.get("Date"))
    if internal_date is None:
        return None

    from_name, from_addr = parse_address(msg.get("From"))
    to_pairs = parse_address_list(msg.get("To"))
    cc_pairs = parse_address_list(msg.get("Cc"))
    bcc_pairs = parse_address_list(msg.get("Bcc"))

    subject_raw = msg.get("Subject")
    subject = str(subject_raw).strip() if subject_raw else None

    body_text, body_html = extract_bodies(msg)
    labels = parse_labels(raw_msg)
    mid = rfc822_message_id(msg) or "unknown"

    return {
        "account_email": account_email,
        "message_id": mid,
        "thread_id": str(msg.get("X-GM-THRID") or msg.get("Thread-Topic") or "") or None,
        "rfc822_message_id": mid if not mid.startswith("synthetic-") else None,
        "from_address": from_addr,
        "from_name": from_name,
        "to_addresses": [a for _, a in to_pairs],
        "cc_addresses": [a for _, a in cc_pairs],
        "bcc_addresses": [a for _, a in bcc_pairs],
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        "internal_date": internal_date,
        "labels": labels,
        "source": "mbox_import",
    }
