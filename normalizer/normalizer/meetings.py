"""Meetings -> people.

Granola recaps and calendar events both carry an attendee list that nothing
ever matched to a person, so calls — the highest-signal conversations there
are — contributed nothing to a profile, nothing to search, and did not count
as contact.

Three passes:
  1. participants  attendee email -> canonical.person, into
                   memory.meeting_participant. Unresolved rows are kept, and
                   become the "who was that?" prompt.
  2. interactions  one canonical.interaction per resolved participant, so a
                   meeting settles follow-ups and satisfies cadences like any
                   other contact.
  3. (the UI reads the unresolved rows directly)

Granola is preferred over the calendar for the same meeting: the calendar
knows who and when, Granola also knows what was said. A calendar event that
duplicates a Granola one contributes no second interaction.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

import asyncpg

from .config import Config

log = logging.getLogger(__name__)

RAW_SOURCE = "memory.meeting_participant"
CHANNEL = "meeting"

# A calendar event within this window of a Granola recap you both attended is
# taken to be the same meeting. Generous because Granola timestamps the
# recording, the calendar timestamps the invite, and calls start late.
DEDUPE_WINDOW = "interval '3 hours'"


def _as_list(blob: Any) -> list:
    """asyncpg hands jsonb back as a STRING in these pools — no codec is
    registered (same trap documented at normalizer/gmail.py:273). Iterating
    the string directly yields characters, every one of which fails the dict
    check, so every attendee list silently comes back empty.
    """
    if isinstance(blob, str):
        try:
            blob = json.loads(blob)
        except (ValueError, TypeError):
            return []
    return blob if isinstance(blob, list) else []


def _clean_email(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    e = value.strip().lower()
    # Room and equipment "attendees" are not people.
    if not e or "@" not in e or e.startswith("c_") and "resource.calendar.google.com" in e:
        return None
    if "resource.calendar.google.com" in e or e.endswith("calendar.google.com"):
        return None
    return e


async def own_emails(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch("SELECT lower(email) AS email FROM raw.gmail_account")
    return {r["email"] for r in rows if r["email"]}


def _granola_attendees(blob: Any) -> Iterable[tuple[str, str | None]]:
    """Granola stores [{name, email}]."""
    for a in _as_list(blob):
        if not isinstance(a, dict):
            continue
        email = _clean_email(a.get("email"))
        if email:
            yield email, (a.get("name") or None)


def _gcal_attendees(blob: Any) -> Iterable[tuple[str, str | None, bool]]:
    """Google stores its own attendee objects, which carry a `self` flag and
    mark rooms as resources."""
    for a in _as_list(blob):
        if not isinstance(a, dict):
            continue
        if a.get("resource"):
            continue
        email = _clean_email(a.get("email"))
        if email:
            yield email, (a.get("displayName") or None), bool(a.get("self"))


# Resolve an attendee email to a person, collapsing one merge hop. Email
# identities are lowercased at write time (normalizer/gmail.py), so an exact
# match is correct and an ILIKE would only be slower.
_RESOLVE_SQL = """
SELECT lower(i.source_id) AS email,
       COALESCE(p.merged_into, p.id) AS person_id
  FROM canonical.identity i
  JOIN canonical.person p ON p.id = i.person_id
 WHERE i.source = 'email'
   AND p.deleted_at IS NULL
   AND lower(i.source_id) = ANY($1::text[])
"""

_UPSERT_SQL = """
INSERT INTO memory.meeting_participant
  (source, source_ref, occurred_at, title, attendee_email, attendee_name,
   person_id, is_self)
VALUES ($1,$2,$3,$4,$5,$6,$7::uuid,$8)
ON CONFLICT (source, source_ref, attendee_email) DO UPDATE SET
  -- Fill in a person once we learn them; never unset one that is already
  -- there, so a manual assignment from the UI survives the next pass.
  person_id     = COALESCE(memory.meeting_participant.person_id, EXCLUDED.person_id),
  attendee_name = COALESCE(memory.meeting_participant.attendee_name, EXCLUDED.attendee_name),
  title         = COALESCE(memory.meeting_participant.title, EXCLUDED.title),
  occurred_at   = EXCLUDED.occurred_at
"""


async def sync_participants(pool: asyncpg.Pool) -> tuple[int, int, int]:
    """Returns (rows_seen, resolved, unresolved)."""
    seen = resolved = unresolved = 0

    async with pool.acquire() as conn:
        mine = await own_emails(conn)

        recaps = await conn.fetch(
            """
            SELECT id::text AS ref, title, meeting_date, attendees
              FROM memory.meeting_recap
             WHERE meeting_date IS NOT NULL
               -- A recap is of a call that happened, but a bad date would
               -- otherwise plant contact in the future. Same rule both sides.
               AND meeting_date <= now()
            """
        )
        events = await conn.fetch(
            """
            SELECT id::text AS ref, summary AS title, start_ts, attendees
              FROM raw.gcal_event
             WHERE start_ts IS NOT NULL
               -- Only meetings that have ALREADY happened. The calendar is
               -- full of future invitations, and a meeting next Tuesday is
               -- not contact: counting it would settle a follow-up before the
               -- call, and make someone look recently-spoken-to on the
               -- strength of an appointment.
               AND start_ts <= now()
               AND COALESCE(status, '') <> 'cancelled'
               -- You declining is the opposite of having met them.
               AND COALESCE(self_response, '') <> 'declined'
               -- An event with no other attendee is a personal block, not a
               -- meeting with someone.
               AND jsonb_array_length(COALESCE(attendees, '[]'::jsonb)) > 1
            """
        )

    rows: list[tuple] = []
    emails: set[str] = set()

    for r in recaps:
        for email, name in _granola_attendees(r["attendees"]):
            emails.add(email)
            rows.append(("granola", r["ref"], r["meeting_date"], r["title"],
                         email, name, email in mine))
    for e in events:
        for email, name, is_self in _gcal_attendees(e["attendees"]):
            emails.add(email)
            rows.append(("gcal", e["ref"], e["start_ts"], e["title"],
                         email, name, is_self or email in mine))

    if not rows:
        return 0, 0, 0

    async with pool.acquire() as conn:
        found = await conn.fetch(_RESOLVE_SQL, list(emails))
        by_email = {r["email"]: r["person_id"] for r in found}

        payload = []
        for source, ref, when, title, email, name, is_self in rows:
            seen += 1
            pid = None if is_self else by_email.get(email)
            if is_self:
                pass
            elif pid:
                resolved += 1
            else:
                unresolved += 1
            payload.append((source, ref, when, title, email, name,
                            str(pid) if pid else None, is_self))

        async with conn.transaction():
            await conn.executemany(_UPSERT_SQL, payload)

    return seen, resolved, unresolved


# One interaction per resolved participant.
#
# direction: a meeting is mutual, but the column only allows inbound/outbound,
# so we say outbound when it is on your calendar as something you were part of
# — you showed up. It is the honest half of a binary that does not fit.
#
# The NOT EXISTS is the Granola-wins rule: a calendar row contributes nothing
# when a Granola participant for the same person sits within the window,
# because Granola's row carries the actual notes as the body.
_INTERACTIONS_SQL = f"""
INSERT INTO canonical.interaction
  (person_id, channel, direction, occurred_at, body, raw_source, raw_id)
SELECT mp.person_id,
       $1,
       'outbound',
       mp.occurred_at,
       CASE WHEN mp.source = 'granola'
            THEN COALESCE(NULLIF(mr.recap, ''), NULLIF(mr.summary, ''), mp.title)
            ELSE mp.title
       END,
       $2,
       mp.id
  FROM memory.meeting_participant mp
  LEFT JOIN memory.meeting_recap mr
    ON mp.source = 'granola' AND mr.id::text = mp.source_ref
  LEFT JOIN canonical.interaction c
    ON c.raw_source = $2 AND c.raw_id = mp.id
 WHERE mp.person_id IS NOT NULL
   AND mp.is_self = false
   AND c.id IS NULL
   AND NOT (
     mp.source = 'gcal' AND EXISTS (
       SELECT 1 FROM memory.meeting_participant g
        WHERE g.source = 'granola'
          AND g.person_id = mp.person_id
          AND g.occurred_at BETWEEN mp.occurred_at - {DEDUPE_WINDOW}
                                AND mp.occurred_at + {DEDUPE_WINDOW}
     )
   )
ON CONFLICT (raw_source, raw_id) DO NOTHING
"""


async def sync_interactions(pool: asyncpg.Pool, cfg: Config) -> int:
    async with pool.acquire() as conn:
        result = await conn.execute(_INTERACTIONS_SQL, CHANNEL, RAW_SOURCE)
    try:
        return int(str(result).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


# Backfill the body of a meeting interaction once Granola's LLM recap lands.
# The recap is written asynchronously after ingest, so an interaction created
# in between would otherwise keep the bare calendar title forever — and the
# body is what gets embedded.
_BACKFILL_BODY_SQL = """
UPDATE canonical.interaction i
   SET body = COALESCE(NULLIF(mr.recap, ''), NULLIF(mr.summary, ''), i.body)
  FROM memory.meeting_participant mp
  JOIN memory.meeting_recap mr ON mr.id::text = mp.source_ref
 WHERE i.raw_source = $1
   AND i.raw_id = mp.id
   AND mp.source = 'granola'
   AND COALESCE(NULLIF(mr.recap, ''), NULLIF(mr.summary, '')) IS NOT NULL
   AND (i.body IS NULL OR i.body = mp.title)
"""


async def backfill_bodies(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        result = await conn.execute(_BACKFILL_BODY_SQL, RAW_SOURCE)
    try:
        return int(str(result).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0
