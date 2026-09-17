"""Reading WhatsApp's ChatStorage.sqlite into the same shape the live bridge
writes.

This is a Core Data store, so the column names are the ugly Z-prefixed ones and
they DRIFT between WhatsApp versions. Nothing here assumes a column exists:
every table is probed first and missing columns come back as None, so an
unfamiliar version produces a clear message instead of a KeyError halfway
through a 200,000-row import.

The output rows match raw.whatsapp_message exactly, so the normalizer cannot
tell an imported message from a live one.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

# Core Data counts seconds from 2001-01-01, not the Unix epoch.
CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def coredata_to_dt(value: Any) -> datetime | None:
    """Core Data seconds -> datetime, or None if the value cannot be one.

    Range-checked on purpose. Some rows carry a Unix timestamp instead, which
    would silently land in 2055 and poison every follow-up date and cadence
    computed from it. A dropped timestamp is recoverable; a wrong one is not.
    """
    if value is None:
        return None
    try:
        secs = float(value)
    except (TypeError, ValueError):
        return None
    # NaN and inf survive float() and would raise inside timedelta.
    if not math.isfinite(secs) or secs <= 0:
        return None
    try:
        dt = CORE_DATA_EPOCH + timedelta(seconds=secs)
    except (OverflowError, OSError):
        return None
    now = datetime.now(timezone.utc)
    # WhatsApp launched in 2009; a day of future slack covers clock skew.
    if dt.year < 2009 or dt > now + timedelta(days=1):
        return None
    return dt


def normalize_jid(jid: str | None) -> str | None:
    """Mirror of jid.js normalizeJid — strips the device suffix.

    Must agree with the bridge, or the same person imports under a second
    identity.
    """
    if not jid or "@" not in jid:
        return None
    user, _, server = jid.partition("@")
    user = user.split(":", 1)[0].split("_", 1)[0]
    if not user:
        return None
    return f"{user}@{server}"


def is_group(jid: str | None) -> bool:
    return bool(jid) and jid.endswith("@g.us")


def is_ignorable(jid: str | None) -> bool:
    if not jid:
        return True
    return jid == "status@broadcast" or jid.endswith("@broadcast") or jid.endswith("@newsletter")


def to_e164(jid: str | None) -> str | None:
    n = normalize_jid(jid)
    if not n or not n.endswith("@s.whatsapp.net"):
        return None
    user = n.split("@", 1)[0]
    return user if user.isdigit() and 5 <= len(user) <= 20 else None


def chat_key_for(jid: str | None) -> str | None:
    """Mirror of jid.js chatKeyFor. Groups keep the jid, phone chats reduce to
    bare digits, hidden ids become 'lid:<n>'."""
    n = normalize_jid(jid)
    if not n or is_ignorable(n):
        return None
    if is_group(n):
        return n
    if n.endswith("@lid"):
        return f"lid:{n.split('@', 1)[0]}"
    return to_e164(n)


# ZMESSAGETYPE is only a fallback: the media item's MIME type is far more
# reliable, and this mapping shifts between WhatsApp releases.
_TYPE_FALLBACK = {
    0: "text",
    1: "image",
    2: "video",
    3: "audio",
    4: "contact_card",
    5: "location",
    7: "text",       # link preview — still a text message
    8: "document",
    11: "contact_card",
    14: "other",     # deleted-for-everyone tombstone
    15: "sticker",
}


def kind_for(message_type: Any, mime: str | None, has_media: bool) -> str:
    """What sort of message this is.

    MIME wins where present. The distinction that matters most is voice note
    versus shared audio: only a voice note gets transcribed, and WhatsApp sends
    voice notes as Opus in an OGG container.
    """
    m = (mime or "").split(";")[0].strip().lower()
    if m:
        if m in ("audio/ogg", "audio/opus", "audio/ogg; codecs=opus"):
            return "voice"
        if m.startswith("audio/"):
            return "audio"
        if m.startswith("image/"):
            return "sticker" if m == "image/webp" else "image"
        if m.startswith("video/"):
            return "video"
        if m.startswith("text/x-vcard") or m == "text/vcard":
            return "contact_card"
        if m:
            return "document"
    try:
        t = int(message_type)
    except (TypeError, ValueError):
        return "other"
    kind = _TYPE_FALLBACK.get(t, "other")
    # A type that claims media with no media item is really just text.
    if kind in ("image", "video", "audio", "document", "sticker") and not has_media:
        return "other"
    return kind


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


def _sel(cols: set[str], *names: str) -> str:
    """SELECT-list entry for the first column that exists, else NULL.

    This is what makes the importer survive schema drift instead of dying on
    an unfamiliar WhatsApp version.
    """
    for n in names:
        if n in cols:
            return n
    # Bare NULL, no alias: every caller supplies its own "AS name". Emitting
    # one here produced "NULL AS ZFOO AS name", which is a syntax error — and
    # it only fires on a store that is missing a column, i.e. precisely the
    # drift case this helper exists to survive.
    return "NULL"


REQUIRED_TABLES = ("ZWAMESSAGE", "ZWACHATSESSION")


def inspect(con: sqlite3.Connection) -> dict[str, Any]:
    """What this store actually looks like — for diagnosing an unknown version
    before trusting an import."""
    tables = [
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    out: dict[str, Any] = {"tables": tables, "columns": {}, "counts": {}}
    for t in ("ZWAMESSAGE", "ZWACHATSESSION", "ZWAGROUPMEMBER", "ZWAMEDIAITEM", "ZWAPROFILEPUSHNAME"):
        cols = table_columns(con, t)
        if cols:
            out["columns"][t] = sorted(cols)
            try:
                out["counts"][t] = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            except sqlite3.DatabaseError:
                pass
    mcols = table_columns(con, "ZWAMESSAGE")
    if "ZMESSAGETYPE" in mcols:
        out["message_type_histogram"] = {
            str(r[0]): r[1]
            for r in con.execute(
                "SELECT ZMESSAGETYPE, count(*) FROM ZWAMESSAGE GROUP BY 1 ORDER BY 2 DESC"
            )
        }
    return out


def chat_sessions(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """Z_PK -> chat. ZPARTNERNAME is the address-book name (or the group
    subject), which is the best-quality name anywhere in this import."""
    cols = table_columns(con, "ZWACHATSESSION")
    if not cols:
        raise LookupError("ZWACHATSESSION missing — not a WhatsApp ChatStorage database")
    sql = f"""
        SELECT Z_PK,
               {_sel(cols, 'ZCONTACTJID')} AS jid,
               {_sel(cols, 'ZPARTNERNAME')} AS partner_name,
               {_sel(cols, 'ZSESSIONTYPE')} AS session_type
          FROM ZWACHATSESSION
    """
    out: dict[int, dict[str, Any]] = {}
    for r in con.execute(sql):
        out[r[0]] = {"jid": normalize_jid(r[1]), "partner_name": r[2], "session_type": r[3]}
    return out


def group_members(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """Z_PK -> the sender behind a group message."""
    cols = table_columns(con, "ZWAGROUPMEMBER")
    if not cols:
        return {}
    sql = f"""
        SELECT Z_PK,
               {_sel(cols, 'ZMEMBERJID')} AS jid,
               {_sel(cols, 'ZCONTACTNAME')} AS name
          FROM ZWAGROUPMEMBER
    """
    return {r[0]: {"jid": normalize_jid(r[1]), "name": r[2]} for r in con.execute(sql)}


def media_items(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """Message Z_PK -> its media, keyed by the message so lookup is direct."""
    cols = table_columns(con, "ZWAMEDIAITEM")
    if not cols or "ZMESSAGE" not in cols:
        return {}
    sql = f"""
        SELECT ZMESSAGE,
               {_sel(cols, 'ZVCARDSTRING')} AS mime,
               {_sel(cols, 'ZMEDIALOCALPATH')} AS local_path,
               {_sel(cols, 'ZTITLE')} AS title
          FROM ZWAMEDIAITEM
         WHERE ZMESSAGE IS NOT NULL
    """
    return {
        r[0]: {"mime": r[1], "local_path": r[2], "title": r[3]}
        for r in con.execute(sql)
    }


def push_names(con: sqlite3.Connection) -> dict[str, str]:
    cols = table_columns(con, "ZWAPROFILEPUSHNAME")
    if not cols:
        return {}
    sql = f"""
        SELECT {_sel(cols, 'ZJID')} AS jid, {_sel(cols, 'ZPUSHNAME')} AS name
          FROM ZWAPROFILEPUSHNAME
    """
    out: dict[str, str] = {}
    for jid, name in con.execute(sql):
        n = normalize_jid(jid)
        if n and name:
            out[n] = name
    return out


def iter_messages(
    con: sqlite3.Connection,
    *,
    self_jid: str | None = None,
    only_chat: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Every importable message, shaped for raw.whatsapp_message.

    Skipped, and why:
      - no ZSTANZAID: group system events ("X joined"), which are not messages
        and have no id to deduplicate on
      - unusable timestamp: see coredata_to_dt
      - status, broadcast and channel chats: never a conversation with a person
    """
    mcols = table_columns(con, "ZWAMESSAGE")
    for t in REQUIRED_TABLES:
        if not table_columns(con, t):
            raise LookupError(f"{t} missing — not a WhatsApp ChatStorage database")

    sessions = chat_sessions(con)
    members = group_members(con)
    media = media_items(con)

    sql = f"""
        SELECT Z_PK,
               {_sel(mcols, 'ZCHATSESSION')} AS session_pk,
               {_sel(mcols, 'ZSTANZAID')} AS stanza_id,
               {_sel(mcols, 'ZISFROMME')} AS is_from_me,
               {_sel(mcols, 'ZMESSAGEDATE')} AS message_date,
               {_sel(mcols, 'ZSENTDATE')} AS sent_date,
               {_sel(mcols, 'ZTEXT')} AS text,
               {_sel(mcols, 'ZMESSAGETYPE')} AS message_type,
               {_sel(mcols, 'ZGROUPMEMBER')} AS group_member_pk,
               {_sel(mcols, 'ZFROMJID')} AS from_jid,
               {_sel(mcols, 'ZTOJID')} AS to_jid
          FROM ZWAMESSAGE
         ORDER BY Z_PK
    """

    for row in con.execute(sql):
        (pk, session_pk, stanza_id, is_from_me, message_date, sent_date,
         text, message_type, group_member_pk, from_jid, to_jid) = row

        if not stanza_id:
            continue

        session = sessions.get(session_pk) or {}
        chat_jid = session.get("jid") or normalize_jid(to_jid if is_from_me else from_jid)
        if not chat_jid or is_ignorable(chat_jid):
            continue
        chat_key = chat_key_for(chat_jid)
        if not chat_key:
            continue
        if only_chat and chat_jid != only_chat:
            continue

        # ZMESSAGEDATE is usually right; ZSENTDATE covers rows where it is 0.
        occurred = coredata_to_dt(message_date) or coredata_to_dt(sent_date)
        if occurred is None:
            continue

        from_me = bool(is_from_me)
        group = is_group(chat_jid)
        if group:
            member = members.get(group_member_pk) or {}
            sender_jid = self_jid if from_me else member.get("jid")
        else:
            sender_jid = self_jid if from_me else chat_jid

        item = media.get(pk) or {}
        kind = kind_for(message_type, item.get("mime"), bool(item))
        body = (text or "").strip() or None
        if body is None and item.get("title"):
            body = str(item["title"]).strip() or None

        yield {
            "chat_jid": chat_jid,
            "chat_key": chat_key,
            "source_message_id": str(stanza_id),
            "from_me": from_me,
            "sender_jid": sender_jid,
            "sender_phone_e164": to_e164(sender_jid),
            "message_date": occurred.isoformat(),
            "kind": kind,
            "text": body,
            "is_group": group,
            "chat_title": session.get("partner_name"),
            # Scalars only. The Core Data blobs on these rows would add
            # hundreds of megabytes of JSONB for nothing.
            "payload": {
                "origin": "iphone_backup",
                "z_pk": pk,
                "message_type": message_type,
                "mime": item.get("mime"),
                "media_local_path": item.get("local_path"),
            },
        }


def contacts_from(
    con: sqlite3.Connection, *, self_jid: str | None = None
) -> Iterator[dict[str, Any]]:
    """Contact rows, carrying the one thing the live bridge can never learn:
    the name from the phone's own address book."""
    sessions = chat_sessions(con)
    pushes = push_names(con)
    seen: set[str] = set()

    for s in sessions.values():
        jid = s.get("jid")
        if not jid or is_group(jid) or is_ignorable(jid) or jid == self_jid:
            continue
        if jid in seen:
            continue
        seen.add(jid)
        yield {
            "jid": jid,
            "phone_e164": to_e164(jid),
            "lid": jid if jid.endswith("@lid") else None,
            "notify_name": s.get("partner_name"),
            "push_name": pushes.get(jid),
        }

    for jid, name in pushes.items():
        if jid in seen or is_group(jid) or is_ignorable(jid) or jid == self_jid:
            continue
        seen.add(jid)
        yield {
            "jid": jid,
            "phone_e164": to_e164(jid),
            "lid": jid if jid.endswith("@lid") else None,
            "notify_name": None,
            "push_name": name,
        }


def groups_from(con: sqlite3.Connection) -> Iterator[dict[str, Any]]:
    """Groups seen in the backup, so the allowlist fills in with real titles."""
    for s in chat_sessions(con).values():
        jid = s.get("jid")
        if jid and is_group(jid):
            yield {"chat_jid": jid, "title": s.get("partner_name")}
