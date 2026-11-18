"""iPhone contacts → people.

Reads raw.iphone_contact (filled by the Setup page upload) and applies the
rules the user chose on 2026-09-17:

- a card matching NOBODY becomes a new person;
- a card whose email or personal number matches an existing person is
  attached to that person — same number, same human;
- a card matching SEVERAL existing people joins the one with the most
  history, and the others are raised against that person for review. They
  are almost always one human split across sources: on the first real
  upload (2026-09-17), 371 cards matched both a WhatsApp and a Telegram or
  LinkedIn person holding the very same number. The original rule — a new
  person plus a review item against each — made 371 extra people and 772
  review items instead of ~370 duplicate pairs.

One refinement protects that rule: a number found on two or more of the
user's cards is SHARED (a family landline, an office switchboard, or one
person saved twice). A match resting only on a shared number is sent to
review instead of attaching, or the second card on a landline would be
silently merged into the first card's person.

Matching evidence: the card's own iphone_contact identity (a re-upload),
email identities, WhatsApp identities (international digits), Telegram
accounts' phone numbers, and phone signals written by structured imports.
Numbers the extraction model found in message text are NOT evidence: a
message can mention someone else's number.

What a card adds to its person: the iphone_contact identity, emails nobody
has claimed, every number as a phone signal, a birthday when the person has
none (year-less birthdays are skipped), and the card's name when the current
one is only a placeholder. Names the user may have typed are never replaced —
nothing records which those are. Company, title and notes stay on the raw
row, where the person page and the profile builder read them.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

import asyncpg

log = logging.getLogger(__name__)

IDENTITY_SOURCE = "iphone_contact"
SIGNAL_SOURCE = "iphone_contacts"
MERGE_SOURCE = "iphone_contacts"
BATCH = 2000
# Phone signals written by structured imports: each is a person's own number.
STRUCTURED_PHONE_SOURCES = ("whatsapp", "linkedin_import", SIGNAL_SOURCE)
FALLBACK_NAME = "Unknown contact"

_NUMBER_NAME_RE = re.compile(r"^[+\d\s().-]+$")
_SPLIT_LOCAL_RE = re.compile(r"[._\-]+")


# --- pure decisions -----------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    action: str                     # keep | attach | create | create_and_review
    person_id: str | None           # the person the card lands on, when already known
    review: tuple[str, ...] = ()    # people to raise a merge candidate against


def best_person(people: Iterable[str], history: dict[str, int] | None = None) -> str:
    """The person with the most interactions; person id breaks ties, so the
    same inputs always pick the same person."""
    history = history or {}
    return sorted(people, key=lambda p: (-history.get(p, 0), p))[0]


def decide(own: str | None, strong: Iterable[str], weak: Iterable[str],
           history: dict[str, int] | None = None) -> Decision:
    """Where a card goes.

    own     — the person already holding this card's identity (a re-upload)
    strong  — people matched by email or by a number only this card carries
    weak    — people matched only through a shared number
    history — interaction counts, to pick among several strong matches
    """
    strong_set, weak_set = set(strong), set(weak) - set(strong)
    if own:
        # A re-upload stays put. Anyone else it now matches is a question for
        # review, never a silent move.
        return Decision("keep", own, tuple(sorted((strong_set | weak_set) - {own})))
    if strong_set:
        target = best_person(strong_set, history)
        return Decision("attach", target, tuple(sorted((strong_set | weak_set) - {target})))
    if not weak_set:
        return Decision("create", None)
    # Only a shared number (a landline, a switchboard): probably a different
    # human, so a person of its own, with the match left for review.
    return Decision("create_and_review", None, tuple(sorted(weak_set)))


@dataclass
class Maps:
    """Who already holds each identifier. Loaded once per run, then updated as
    cards land, so a later card in the same batch sees an earlier one."""
    own: dict[str, str] = field(default_factory=dict)             # card uid → person
    email: dict[str, str] = field(default_factory=dict)           # email → person
    whatsapp: dict[str, str] = field(default_factory=dict)        # digits → person
    telegram: dict[str, set[str]] = field(default_factory=dict)   # digits → people
    signals: dict[str, set[str]] = field(default_factory=dict)    # digits → people


def match_card(uid: str, emails: Iterable[str], phones: Iterable[str],
               maps: Maps, shared_numbers: set[str]) -> tuple[str | None, set[str], set[str]]:
    """(own person, strong matches, weak matches) for one card."""
    strong: set[str] = set()
    weak: set[str] = set()
    for e in emails:
        if e in maps.email:
            strong.add(maps.email[e])
    for n in phones:
        people = set(maps.telegram.get(n, ())) | set(maps.signals.get(n, ()))
        if n in maps.whatsapp:
            people.add(maps.whatsapp[n])
        (weak if n in shared_numbers else strong).update(people)
    return maps.own.get(uid), strong, weak - strong


def is_placeholder_name(name: str | None, emails: Iterable[str] = ()) -> bool:
    """A name nobody chose: empty, a bare phone number, 'Telegram user N', an
    email address, or a name derived from one ('dana.lee@…' → 'Dana Lee')."""
    n = (name or "").strip()
    if not n or n == FALLBACK_NAME:
        return True
    if n.lower().startswith("telegram user ") or "@" in n or _NUMBER_NAME_RE.match(n):
        return True
    for e in emails:
        local = (e or "").split("@", 1)[0]
        derived = " ".join(p.capitalize() for p in _SPLIT_LOCAL_RE.split(local) if p)
        if derived and n == derived:
            return True
    return False


# --- database pass ------------------------------------------------------------

async def _load_maps(conn: asyncpg.Connection) -> Maps:
    maps = Maps()
    for r in await conn.fetch(
        """
        SELECT i.source, i.source_id, COALESCE(p.merged_into, p.id)::text AS person_id
          FROM canonical.identity i
          JOIN canonical.person p ON p.id = i.person_id
         WHERE i.source IN ('iphone_contact', 'email', 'whatsapp') AND p.deleted_at IS NULL
        """
    ):
        target = {"iphone_contact": maps.own, "email": maps.email, "whatsapp": maps.whatsapp}[r["source"]]
        target[r["source_id"]] = r["person_id"]
    for r in await conn.fetch(
        """
        SELECT regexp_replace(u.phone, '\\D', '', 'g') AS digits,
               COALESCE(p.merged_into, p.id)::text AS person_id
          FROM raw.telegram_user u
          JOIN canonical.identity i ON i.source = 'telegram' AND i.source_id = u.source_user_id::text
          JOIN canonical.person p ON p.id = i.person_id
         WHERE COALESCE(u.phone, '') <> '' AND p.deleted_at IS NULL
        """
    ):
        maps.telegram.setdefault(r["digits"], set()).add(r["person_id"])
    for r in await conn.fetch(
        """
        SELECT s.value, COALESCE(p.merged_into, p.id)::text AS person_id
          FROM memory.extracted_signal s
          JOIN canonical.person p ON p.id = s.person_id
         WHERE s.signal_type = 'phone' AND s.confidence = 'high'
           AND s.source = ANY($1::text[]) AND p.deleted_at IS NULL
        """,
        list(STRUCTURED_PHONE_SOURCES),
    ):
        maps.signals.setdefault(r["value"], set()).add(r["person_id"])
    return maps


async def sync_contacts(pool: asyncpg.Pool) -> dict[str, int]:
    stats = {"cards": 0, "created": 0, "attached": 0, "kept": 0, "reviewed": 0,
             "names_upgraded": 0, "birthdays": 0}
    async with pool.acquire() as conn:
        pending = await conn.fetch(
            """
            SELECT id, uid, display_name, emails, phones, birthday, birthday_has_year,
                   organization, job_title, content_hash
              FROM raw.iphone_contact
             WHERE processed_at IS NULL
             ORDER BY id
             LIMIT $1
            """,
            BATCH,
        )
        if not pending:
            return stats
        maps = await _load_maps(conn)
        shared = {r["n"] for r in await conn.fetch(
            """
            SELECT n FROM raw.iphone_contact, unnest(phones) AS n
             GROUP BY n HAVING count(DISTINCT uid) > 1
            """
        )}

    for card in pending:
        stats["cards"] += 1
        emails, phones = list(card["emails"] or []), list(card["phones"] or [])
        own, strong, weak = match_card(card["uid"], emails, phones, maps, shared)
        history: dict[str, int] = {}
        if not own and len(strong) > 1:
            async with pool.acquire() as conn:
                history = {r["pid"]: r["n"] for r in await conn.fetch(
                    """
                    SELECT person_id::text AS pid, count(*)::int AS n
                      FROM canonical.interaction
                     WHERE person_id = ANY($1::uuid[])
                     GROUP BY person_id
                    """,
                    list(strong),
                )}
        d = decide(own, strong, weak, history)
        async with pool.acquire() as conn:
            async with conn.transaction():
                pid = d.person_id
                if pid is None:
                    pid = str(await conn.fetchval(
                        "INSERT INTO canonical.person (display_name) VALUES ($1) RETURNING id",
                        card["display_name"] or FALLBACK_NAME,
                    ))
                    stats["created"] += 1
                else:
                    stats["attached" if d.action == "attach" else "kept"] += 1

                evidence = json.dumps({
                    "first_seen_source": SIGNAL_SOURCE,
                    "display_name": card["display_name"],
                    "organization": card["organization"],
                    "job_title": card["job_title"],
                })
                await conn.execute(
                    """
                    INSERT INTO canonical.identity (person_id, source, source_id, evidence)
                    VALUES ($1::uuid, $2, $3, $4::jsonb)
                    ON CONFLICT (source, source_id) DO NOTHING
                    """,
                    pid, IDENTITY_SOURCE, card["uid"], evidence,
                )
                for e in emails:
                    if e not in maps.email:
                        await conn.execute(
                            """
                            INSERT INTO canonical.identity (person_id, source, source_id, evidence)
                            VALUES ($1::uuid, 'email', $2, $3::jsonb)
                            ON CONFLICT (source, source_id) DO NOTHING
                            """,
                            pid, e, evidence,
                        )
                for n in phones:
                    await conn.execute(
                        """
                        INSERT INTO memory.extracted_signal
                          (person_id, signal_type, value, confidence, source, evidence)
                        VALUES ($1::uuid, 'phone', $2, 'high', $3, $4::jsonb)
                        ON CONFLICT (person_id, signal_type, value, source) DO UPDATE
                          SET last_seen_at = now()
                        """,
                        pid, n, SIGNAL_SOURCE,
                        json.dumps({"card": card["display_name"], "shared": n in shared}),
                    )

                if d.action in ("attach", "keep") and card["display_name"]:
                    person = await conn.fetchrow(
                        """
                        SELECT p.display_name,
                               array_remove(array_agg(i.source_id) FILTER (WHERE i.source = 'email'), NULL) AS emails
                          FROM canonical.person p
                          LEFT JOIN canonical.identity i ON i.person_id = p.id
                         WHERE p.id = $1::uuid
                         GROUP BY p.display_name
                        """,
                        pid,
                    )
                    if person and is_placeholder_name(person["display_name"], person["emails"] or []):
                        await conn.execute(
                            "UPDATE canonical.person SET display_name = $2 WHERE id = $1::uuid",
                            pid, card["display_name"],
                        )
                        stats["names_upgraded"] += 1

                if card["birthday"] is not None and card["birthday_has_year"]:
                    set_bday = await conn.execute(
                        "UPDATE canonical.person SET birthday = $2 WHERE id = $1::uuid AND birthday IS NULL",
                        pid, card["birthday"],
                    )
                    if set_bday.endswith(" 1"):
                        stats["birthdays"] += 1

                for other in d.review:
                    if other == pid:
                        continue
                    added = await conn.execute(
                        """
                        INSERT INTO memory.merge_candidate
                          (left_person_id, right_person_id, source, confidence, evidence)
                        VALUES ($1::uuid, $2::uuid, $3, 'high', $4::jsonb)
                        ON CONFLICT DO NOTHING
                        """,
                        pid, other, MERGE_SOURCE,
                        json.dumps({"reason": "same number or email in your phone book",
                                    "card": card["display_name"], "numbers": phones,
                                    "emails": emails}),
                    )
                    if added.endswith(" 1"):
                        stats["reviewed"] += 1

                # Only this version of the card: an upload that changed it
                # mid-run keeps it pending for the next run.
                await conn.execute(
                    "UPDATE raw.iphone_contact SET processed_at = now() WHERE id = $1 AND content_hash = $2",
                    card["id"], card["content_hash"],
                )

        maps.own[card["uid"]] = pid
        for e in emails:
            maps.email.setdefault(e, pid)
        for n in phones:
            maps.signals.setdefault(n, set()).add(pid)

    return stats
