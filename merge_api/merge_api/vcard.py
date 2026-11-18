"""Parse vCard files exported from an iPhone into contact records.

What Contacts and iCloud.com export is vCard 3.0: one BEGIN:VCARD … END:VCARD
block per contact, long lines folded onto continuation lines that start with a
space, Apple's "item1." group prefixes on labelled fields, and every contact
photo inlined as base64. Exports from older phones use vCard 2.1, with
quoted-printable values and bare parameter types (TEL;CELL;PREF).

Pure and dependency-free, so it is unit-tested without a database. The Google
Takeout importer (fetchers/gmail_import/vcard.py) reads the same format for
emails and names only; code isn't shared across apps in this repo, so this is
a fuller parser in its own right rather than an import of that one.

Photos are dropped while reading, never accumulated: a phone book with photos
can run to hundreds of megabytes, almost all of it image data.
"""
from __future__ import annotations

import hashlib
import json
import quopri
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Iterable, Iterator

_ESCAPE_RE = re.compile(r"\\(.)")
_UNESCAPED_SEMI_RE = re.compile(r"(?<!\\);")
# Everything from a pause/wait character or an extension onwards: "+1 555 0100,123"
# or "+1 555 0100 ext. 42" is still the number +1 555 0100.
_EXTENSION_RE = re.compile(r"\s*(?:[,;].*|(?:ext\.?|x)\s*\d.*)$", re.IGNORECASE)
_NON_DIGITS_RE = re.compile(r"\D+")
_BDAY_RE = re.compile(r"^(?:(\d{4})|-)-?(\d{2})-?(\d{2})")
# Binary properties: never used, and by far the bulk of an export.
_BINARY_PROPS = frozenset({"PHOTO", "LOGO", "SOUND", "KEY"})
# Apple's placeholder year for a birthday saved without one.
_NO_YEAR = 1604


@dataclass
class Contact:
    uid: str = ""
    display_name: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    nickname: str | None = None
    organization: str | None = None
    job_title: str | None = None
    notes: str | None = None
    birthday: date | None = None
    birthday_has_year: bool = True
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)       # international digits, no '+'
    phones_raw: list[str] = field(default_factory=list)   # as written on the card
    urls: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    social_profiles: list[str] = field(default_factory=list)
    content_hash: str = ""

    def payload(self) -> dict[str, Any]:
        """Parsed fields with no column of their own."""
        return {"urls": self.urls, "addresses": self.addresses,
                "social_profiles": self.social_profiles}


def normalize_phone(raw: str | None, default_country_code: str | None = None) -> str | None:
    """International digits without '+' — the form WhatsApp identities use — or
    None when the number can't be placed in a country.

    '+44 20 …' and '0044 20 …' are international already. A national number
    with a trunk '0' ('081 234 5678') is placed with `default_country_code`
    when one is configured. Anything else is left unmatched rather than
    guessed: a wrong country would attach a contact to the wrong person."""
    if not raw:
        return None
    s = _EXTENSION_RE.sub("", raw.strip())
    digits = _NON_DIGITS_RE.sub("", s)
    if not digits:
        return None
    if s.startswith("+"):
        out = digits
    elif digits.startswith("00"):
        out = digits[2:]
    elif default_country_code and digits.startswith("0"):
        out = default_country_code + digits[1:]
    else:
        return None
    return out if 8 <= len(out) <= 15 else None


def raw_digits(raw: str | None) -> str:
    """The digits of a number as written, extension removed: the key a user's
    fix is stored under, so '8 (916) 123-45-67' and '89161234567' share one."""
    return _NON_DIGITS_RE.sub("", _EXTENSION_RE.sub("", (raw or "").strip()))


def parse_international(value: str | None) -> str | None:
    """A number the user typed in international form ('+7 916 123-45-67') as
    digits, or None. The '+' is required: without it the country is a guess."""
    s = (value or "").strip()
    if not s.startswith("+"):
        return None
    digits = _NON_DIGITS_RE.sub("", s)
    return digits if 8 <= len(digits) <= 15 else None


def suggest_fix(digits: str, default_country_code: str | None = None) -> dict[str, str] | None:
    """A likely reading of a number written without a country code, for the
    user to accept — never applied on its own.

    Only shapes that can't reasonably mean anything else get a suggestion:
    11 digits starting 7 or 8 is Russian/Kazakh, 11 starting 1 is North
    American, 11 starting 66 is Thai. A 10-digit number starting 9 could be
    Russian or Indian, so it gets none. Returns {"action", "label"} plus
    "international" for action "set", or None."""
    d = digits or ""
    if not d:
        return None
    if len(d) <= 7:
        return {"action": "ignore", "label": "Too short to be a phone number"}
    if len(d) == 11 and d.startswith("7"):
        return {"action": "set", "international": d, "label": "Russian numbers written without +"}
    if len(d) == 11 and d.startswith("8"):
        return {"action": "set", "international": "7" + d[1:],
                "label": "Russian numbers written with 8 instead of +7"}
    if len(d) == 11 and d.startswith("1"):
        return {"action": "set", "international": d, "label": "US/Canadian numbers written without +"}
    if len(d) == 11 and d.startswith("66"):
        return {"action": "set", "international": d, "label": "Thai numbers written without +"}
    cc = _NON_DIGITS_RE.sub("", default_country_code or "")
    if cc and d.startswith("0") and 8 <= len(cc) + len(d) - 1 <= 15:
        return {"action": "set", "international": cc + d[1:],
                "label": f"Local numbers (your default country, +{cc})"}
    return None


def _unescape(value: str) -> str:
    return _ESCAPE_RE.sub(lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def _components(value: str) -> list[str]:
    return [_unescape(p).strip() for p in _UNESCAPED_SEMI_RE.split(value)]


def _property_name(line: str) -> str:
    head = re.split(r"[;:]", line, maxsplit=1)[0]
    return head.rsplit(".", 1)[-1].upper()


def _logical_lines(lines: Iterable[str]) -> Iterator[str]:
    """Undo line folding and quoted-printable soft line breaks, and skip binary
    properties (photos) without ever holding their data."""
    pending: str | None = None
    skipping = False
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line[:1] in (" ", "\t"):
            if not skipping and pending is not None:
                pending += line[1:]
            continue
        # vCard 2.1: a quoted-printable value ending in '=' continues on the
        # next line with no leading space.
        if (pending is not None and pending.endswith("=")
                and "QUOTED-PRINTABLE" in pending.split(":", 1)[0].upper()):
            pending = pending[:-1] + line
            continue
        if pending is not None:
            yield pending
        skipping = _property_name(line) in _BINARY_PROPS
        pending = None if skipping else line
    if pending is not None:
        yield pending


def _split_property(line: str) -> tuple[str, dict[str, list[str]], str] | None:
    """NAME;PARAM=a,b;TYPE:value → (NAME, {PARAM: [A, B], TYPE: [...]}, value).
    The value starts at the first colon outside a quoted parameter."""
    in_quotes = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ":" and not in_quotes:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return None
    parts = head.split(";")
    name = parts[0].rsplit(".", 1)[-1].upper()
    params: dict[str, list[str]] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params.setdefault(k.upper(), []).extend(x.strip('"').upper() for x in v.split(","))
        elif p:
            params.setdefault("TYPE", []).append(p.upper())   # vCard 2.1 bare types
    if "QUOTED-PRINTABLE" in params.get("ENCODING", []):
        charset = (params.get("CHARSET") or ["UTF-8"])[0]
        data = quopri.decodestring(value.encode("latin-1", "replace"))
        try:
            value = data.decode(charset, "replace")
        except LookupError:
            value = data.decode("utf-8", "replace")
    return name, params, value


def _parse_birthday(value: str, params: dict[str, list[str]]) -> tuple[date | None, bool]:
    m = _BDAY_RE.match(value.strip())
    if not m:
        return None, True
    year = m.group(1)
    omit = params.get("X-APPLE-OMIT-YEAR", [])
    has_year = year is not None and int(year) != _NO_YEAR and year not in omit
    try:
        return date(int(year) if year else _NO_YEAR, int(m.group(2)), int(m.group(3))), has_year
    except ValueError:
        return None, True


def _apply(c: Contact, name: str, params: dict[str, list[str]], value: str,
           default_country_code: str | None) -> None:
    if name == "FN":
        c.display_name = _unescape(value).strip() or c.display_name
    elif name == "N":
        comps = _components(value) + ["", ""]
        c.family_name = comps[0] or c.family_name
        c.given_name = comps[1] or c.given_name
    elif name == "NICKNAME":
        c.nickname = _components(value.replace(",", ";"))[0] or c.nickname
    elif name == "ORG":
        comps = [x for x in _components(value) if x]
        c.organization = comps[0] if comps else c.organization
    elif name == "TITLE":
        c.job_title = _unescape(value).strip() or c.job_title
    elif name == "NOTE":
        c.notes = _unescape(value).strip() or c.notes
    elif name == "BDAY":
        c.birthday, c.birthday_has_year = _parse_birthday(value, params)
    elif name == "EMAIL":
        addr = _unescape(value).strip().lower()
        if "@" in addr and addr not in c.emails:
            c.emails.append(addr)
    elif name == "TEL":
        raw = _unescape(value).strip()
        if raw and raw not in c.phones_raw:
            c.phones_raw.append(raw)
            n = normalize_phone(raw, default_country_code)
            if n and n not in c.phones:
                c.phones.append(n)
    elif name == "URL":
        url = _unescape(value).strip()
        if url and url not in c.urls:
            c.urls.append(url)
    elif name == "ADR":
        addr = ", ".join(x for x in _components(value) if x)
        if addr and addr not in c.addresses:
            c.addresses.append(addr)
    elif name == "X-SOCIALPROFILE":
        prof = _unescape(value).strip() or ",".join(params.get("X-USER", []))
        if prof and prof not in c.social_profiles:
            c.social_profiles.append(prof)
    elif name == "UID":
        c.uid = _unescape(value).strip() or c.uid


def _digest(obj: Any) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _finish(c: Contact) -> Contact:
    if not c.display_name:
        full = " ".join(x for x in (c.given_name, c.family_name) if x)
        c.display_name = (full or c.nickname or c.organization
                          or (c.emails[0] if c.emails else None)
                          or (c.phones_raw[0] if c.phones_raw else None))
    fields = asdict(c)
    fields.pop("uid")
    fields.pop("content_hash")
    c.content_hash = _digest(fields)
    if not c.uid:
        # No UID on the card: key it by who it is. Editing the card then makes
        # a new row, which the normalizer still reunites by number or email.
        c.uid = "hash:" + _digest([c.display_name, sorted(c.emails), sorted(c.phones_raw)])[:32]
    return c


def parse_vcards(lines: Iterable[str], default_country_code: str | None = None) -> list[Contact]:
    """Every complete card in the file, in order. Accepts any iterable of lines
    — an open file streams, so a large export is never read into memory whole."""
    cc = _NON_DIGITS_RE.sub("", default_country_code or "") or None
    contacts: list[Contact] = []
    cur: Contact | None = None
    for line in _logical_lines(lines):
        upper = line.upper()
        if upper.startswith("BEGIN:VCARD"):
            cur = Contact()
            continue
        if upper.startswith("END:VCARD"):
            if cur is not None:
                contacts.append(_finish(cur))
            cur = None
            continue
        if cur is None:
            continue
        prop = _split_property(line)
        if prop:
            _apply(cur, *prop, cc)
    return contacts


def summarize(contacts: list[Contact]) -> dict[str, int]:
    """Upload summary: how many numbers can be matched to people at all."""
    raw = sum(len(c.phones_raw) for c in contacts)
    placed = sum(len(c.phones) for c in contacts)
    return {"with_phone": sum(1 for c in contacts if c.phones_raw),
            "numbers": raw, "numbers_without_country": max(0, raw - placed)}
