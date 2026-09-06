"""Reading WhatsApp's own "Export Chat" text files.

The fallback when a full iPhone backup will not fit on disk. WhatsApp can email
or AirDrop a single conversation as plain text, a few megabytes for years of
messages when exported "Without Media", so disk space stops being the
constraint.

What it costs, relative to the backup:

  * No message ids. The export has none, so we synthesise a deterministic one
    from the message's own content — same file imported twice is the same ids,
    but the SAME message captured live carries a different id and would
    duplicate. Handled in db.py by refusing to import export rows that overlap
    the live window for a chat.
  * No phone numbers. Senders appear as display names only, so the caller has
    to say which number the file belongs to. There is no way to infer it.
  * No media. Attachments appear as placeholder text and are recorded as the
    right kind with no file.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Iterator

# WhatsApp sprinkles bidirectional control characters through exports; they are
# invisible, and they break every naive regex.
_INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‫‬⁦⁧⁨⁩"), None)

# Two layouts in the wild: iOS brackets the timestamp, Android uses a dash.
_IOS = re.compile(r"^\[(?P<ts>[^\]]+)\]\s(?P<rest>.*)$")
_ANDROID = re.compile(r"^(?P<ts>\d{1,2}[/.]\d{1,2}[/.]\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\s-\s(?P<rest>.*)$")

# Ordered so the unambiguous four-digit-year forms are tried first.
_TS_FORMATS = [
    "%d/%m/%Y, %H:%M:%S", "%m/%d/%Y, %H:%M:%S",
    "%d/%m/%Y, %I:%M:%S %p", "%m/%d/%Y, %I:%M:%S %p",
    "%d/%m/%Y, %H:%M", "%m/%d/%Y, %H:%M",
    "%d/%m/%Y, %I:%M %p", "%m/%d/%Y, %I:%M %p",
    "%d/%m/%y, %H:%M:%S", "%m/%d/%y, %H:%M:%S",
    "%d/%m/%y, %I:%M:%S %p", "%m/%d/%y, %I:%M:%S %p",
    "%d/%m/%y, %H:%M", "%m/%d/%y, %H:%M",
    "%d/%m/%y, %I:%M %p", "%m/%d/%y, %I:%M %p",
    "%d.%m.%Y, %H:%M:%S", "%d.%m.%Y, %H:%M",
    "%Y-%m-%d, %H:%M:%S", "%Y-%m-%d %H:%M:%S",
]

# Localised placeholders WhatsApp writes in place of an attachment.
_MEDIA_MARKERS = {
    "image": ("image omitted", "‎image omitted", "photo omitted"),
    "video": ("video omitted", "gif omitted"),
    "voice": ("audio omitted", "voice message omitted", "ptt omitted"),
    "sticker": ("sticker omitted",),
    "document": ("document omitted",),
    "contact_card": ("contact card omitted",),
    "location": ("location:",),
}
_ATTACHED = re.compile(r"<attached:\s*(?P<name>[^>]+)>", re.I)
_EXT_KIND = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "sticker",
    ".mp4": "video", ".mov": "video", ".gif": "video",
    ".opus": "voice", ".ogg": "voice", ".m4a": "audio", ".mp3": "audio",
    ".pdf": "document", ".docx": "document", ".vcf": "contact_card",
}


def _clean(line: str) -> str:
    return line.translate(_INVISIBLE).replace(" ", " ").rstrip("\r\n")


def _split_header(line: str) -> tuple[str, str] | None:
    """(timestamp text, remainder) when a line starts a new message."""
    for rx in (_IOS, _ANDROID):
        m = rx.match(line)
        if m:
            return m.group("ts").strip(), m.group("rest")
    return None


def detect_timestamp_format(samples: list[str]) -> str | None:
    """Pick the one format that parses EVERY sample in non-decreasing order.

    Day-first and month-first are indistinguishable for the first twelve days
    of a month, so a format cannot be chosen from one line. Requiring it to fit
    the whole file, and to keep the export's chronological order, is what
    separates 3 March from 5 May. Returns None rather than guessing.
    """
    if not samples:
        return None
    for fmt in _TS_FORMATS:
        parsed: list[datetime] = []
        for s in samples:
            try:
                parsed.append(datetime.strptime(s, fmt))
            except ValueError:
                parsed = []
                break
        if not parsed:
            continue
        if all(a <= b for a, b in zip(parsed, parsed[1:])):
            return fmt
    return None


def synthetic_id(chat_key: str, occurred: datetime, sender: str, text: str) -> str:
    """A stable id for a message that has none.

    Deterministic in the message's own content, so re-importing the same export
    is a no-op. Prefixed 'exp:' so an export-derived row is always
    distinguishable from a real WhatsApp id in the database.
    """
    h = hashlib.sha1(
        "\x1f".join([chat_key, occurred.isoformat(), sender, text or ""]).encode("utf-8")
    ).hexdigest()[:20]
    return f"exp:{h}"


def _kind_and_text(body: str) -> tuple[str, str | None]:
    low = body.strip().lower()
    m = _ATTACHED.search(body)
    if m:
        name = m.group("name").strip().lower()
        for ext, kind in _EXT_KIND.items():
            if name.endswith(ext):
                return kind, None
        return "document", None
    for kind, markers in _MEDIA_MARKERS.items():
        for marker in markers:
            if low.startswith(marker) or low == marker.strip():
                return kind, (body.strip() if kind == "location" else None)
    if not body.strip():
        return "other", None
    return "text", body.strip()


def parse(
    text: str,
    *,
    chat_key: str,
    chat_jid: str,
    self_name: str,
    is_group: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield rows shaped exactly like the backup importer's, so both feed the
    same loader and the same raw table.

    System notices — the encryption banner, "X changed the subject", missed
    calls — have no "Sender: " prefix and are skipped: they are not messages
    and would otherwise become interactions attributed to nobody.
    """
    lines = [_clean(l) for l in text.splitlines()]

    headers: list[tuple[str, str]] = []
    for line in lines:
        h = _split_header(line)
        if h:
            headers.append(h)
    fmt = detect_timestamp_format([ts for ts, _ in headers])
    if fmt is None:
        raise ValueError(
            "Could not read the timestamps in this export — no date format fits "
            "every line. Send me the first few lines and I will add it."
        )

    self_norm = self_name.strip().lower()
    current: dict[str, Any] | None = None

    def finish(msg: dict[str, Any] | None) -> Iterator[dict[str, Any]]:
        if msg is None:
            return
        body = "\n".join(msg["lines"]).strip()
        kind, clean_text = _kind_and_text(body)
        occurred = msg["occurred"]
        sender = msg["sender"]
        from_me = sender.strip().lower() == self_norm
        yield {
            "chat_jid": chat_jid,
            "chat_key": chat_key,
            "source_message_id": synthetic_id(chat_key, occurred, sender, clean_text or body),
            "from_me": from_me,
            # An export never reveals a number, so a group sender cannot be
            # identified at all; a DM sender is whoever the chat is with.
            "sender_jid": None if (is_group and not from_me) else (None if from_me else chat_jid),
            "sender_phone_e164": None,
            "message_date": occurred.replace(tzinfo=timezone.utc).isoformat(),
            "kind": kind,
            "text": clean_text,
            "is_group": is_group,
            "chat_title": None,
            "payload": {
                "origin": "chat_export",
                "sender_name": sender,
                "raw_kind": kind,
            },
        }

    for line in lines:
        h = _split_header(line)
        if h is None:
            # A continuation of the previous message. Multi-line messages are
            # common and dropping the tail would silently truncate them.
            if current is not None:
                current["lines"].append(line)
            continue

        yield from finish(current)
        current = None

        ts_text, rest = h
        try:
            occurred = datetime.strptime(ts_text, fmt)
        except ValueError:
            continue

        sender, sep, body = rest.partition(": ")
        if not sep or "\n" in sender or len(sender) > 80:
            # No "Name: " prefix — a system notice, not a message.
            continue
        current = {"occurred": occurred, "sender": sender.strip(), "lines": [body]}

    yield from finish(current)
