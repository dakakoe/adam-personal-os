"""Gmail-side normalization: raw.gmail_message → canonical.interaction
with email-keyed identity resolution.

Identity model:
  - canonical.identity (source='email', source_id=<lowercased address>)
  - one identity per distinct counterparty email we've seen
  - canonical.person created lazily (display_name = best from_name we've seen,
    falling back to the email local-part Title-Cased)

Direction:
  - 'outbound' when from_address is in raw.gmail_account (one of OUR accounts)
  - 'inbound' otherwise

Body for embedding/search:
  - "Subject: <subj>\n\n<plain text body>"  (HTML stripped only if no text part)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import asyncpg

from .config import Config

log = logging.getLogger(__name__)


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    """Quick & dirty HTML → text for messages with no text/plain part. Not a
    full parser — collapses tags + whitespace; good enough for memory recall."""
    text = _HTML_TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", text).strip()


def _email_to_display_name(addr: str, hinted_name: str | None) -> str:
    if hinted_name:
        return hinted_name.strip() or addr
    local = addr.split("@", 1)[0]
    # "jane.doe" or "jane_doe" → "Jane Doe"
    return " ".join(part.capitalize() for part in re.split(r"[._\-]+", local) if part) or addr


async def _own_emails(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch("SELECT email FROM raw.gmail_account")
    return {r["email"].lower() for r in rows}


async def sync_persons_email(pool: asyncpg.Pool) -> tuple[int, int]:
    """Pass 1 (gmail): for every distinct counterparty email in
    raw.gmail_message, ensure a canonical.identity row exists. Counts
    (addresses_seen, identities_created)."""
    async with pool.acquire() as conn:
        own = await _own_emails(conn)
        rows = await conn.fetch(
            """
            -- A counterparty is the from_address (when not us) and every
            -- to/cc address (when not us). We pick the most-recent from_name
            -- we've seen as the display name hint.
            WITH parts AS (
              SELECT lower(from_address) AS addr, from_name AS hinted_name, internal_date
                FROM raw.gmail_message
               WHERE from_address IS NOT NULL
              UNION ALL
              SELECT lower(t) AS addr, NULL AS hinted_name, internal_date
                FROM raw.gmail_message, UNNEST(to_addresses) AS t
              UNION ALL
              SELECT lower(c) AS addr, NULL AS hinted_name, internal_date
                FROM raw.gmail_message, UNNEST(cc_addresses) AS c
            ),
            ranked AS (
              SELECT addr, hinted_name,
                     ROW_NUMBER() OVER (
                       PARTITION BY addr
                       ORDER BY (hinted_name IS NOT NULL) DESC, internal_date DESC
                     ) AS rn
              FROM parts
              WHERE addr IS NOT NULL AND addr LIKE '%@%'
            )
            SELECT addr, hinted_name FROM ranked WHERE rn = 1
            """
        )

    own_skipped = 0
    seen = 0
    created = 0
    for r in rows:
        addr = r["addr"]
        if addr in own:
            own_skipped += 1
            continue
        seen += 1
        display_name = _email_to_display_name(addr, r["hinted_name"])
        evidence = {"first_seen_source": "gmail", "name_hint": r["hinted_name"]}
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    WITH existing AS (
                        SELECT person_id FROM canonical.identity
                         WHERE source = 'email' AND source_id = $1
                         LIMIT 1
                    ),
                    new_person AS (
                        INSERT INTO canonical.person (display_name)
                        SELECT $2
                        WHERE NOT EXISTS (SELECT 1 FROM existing)
                        RETURNING id
                    ),
                    new_identity AS (
                        INSERT INTO canonical.identity (person_id, source, source_id, evidence)
                        SELECT id, 'email', $1, $3::jsonb FROM new_person
                        RETURNING person_id
                    )
                    SELECT person_id, FALSE AS created FROM existing
                    UNION ALL
                    SELECT person_id, TRUE AS created FROM new_identity
                    """,
                    addr, display_name, json.dumps(evidence, default=str),
                )
        if row and row["created"]:
            created += 1

    log.info("sync_persons_email: seen=%d created=%d (skipped %d own addresses)",
             seen, created, own_skipped)
    return seen, created


async def _email_identity_map(conn: asyncpg.Connection) -> dict[str, str]:
    """Snapshot of email→person_id with merge-collapse for downstream lookups."""
    rows = await conn.fetch(
        """
        SELECT i.source_id AS addr,
               COALESCE(p.merged_into, p.id)::text AS person_id
          FROM canonical.identity i
          JOIN canonical.person p ON p.id = i.person_id
         WHERE i.source = 'email'
        """
    )
    return {r["addr"]: r["person_id"] for r in rows}


def _build_body(subject: str | None, text: str | None, html: str | None) -> str | None:
    """Compose a single body field for canonical.interaction. We keep the
    subject line first (search-friendly), then the most useful body text."""
    body = (text or "").strip()
    if not body and html:
        body = _strip_html(html)
    pieces = []
    if subject:
        pieces.append(f"Subject: {subject.strip()}")
    if body:
        pieces.append(body)
    out = "\n\n".join(pieces).strip()
    return out or None


async def sync_persons_from_google_contacts(pool: asyncpg.Pool) -> tuple[int, int, int]:
    """For every email in raw.google_contact, ensure canonical.identity
    (source='email') exists; upgrade canonical.person.display_name when the
    existing one is synthetic and the Google contact has a real name.

    Returns (emails_seen, persons_created, names_upgraded).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT display_name, given_name, family_name, emails, phones,
                   organization, job_title
              FROM raw.google_contact
             WHERE array_length(emails, 1) > 0
            """
        )

    seen = persons_created = names_upgraded = 0
    for r in rows:
        # Prefer FN; fall back to "Given Family"
        name_hint = r["display_name"] or " ".join(
            x for x in (r["given_name"], r["family_name"]) if x
        ) or None

        for addr in r["emails"]:
            addr = (addr or "").lower().strip()
            if not addr or "@" not in addr:
                continue
            seen += 1
            display = _email_to_display_name(addr, name_hint)
            evidence = {
                "first_seen_source": "google_contacts",
                "name_hint": name_hint,
                "organization": r["organization"],
                "job_title": r["job_title"],
            }
            async with pool.acquire() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        """
                        WITH existing AS (
                          SELECT i.person_id, p.display_name AS old_name
                            FROM canonical.identity i
                            JOIN canonical.person p ON p.id = i.person_id
                           WHERE i.source = 'email' AND i.source_id = $1
                           LIMIT 1
                        ),
                        new_person AS (
                          INSERT INTO canonical.person (display_name)
                          SELECT $2
                          WHERE NOT EXISTS (SELECT 1 FROM existing)
                          RETURNING id
                        ),
                        new_identity AS (
                          INSERT INTO canonical.identity (person_id, source, source_id, evidence)
                          SELECT id, 'email', $1, $3::jsonb FROM new_person
                          RETURNING person_id
                        )
                        SELECT person_id, old_name, FALSE AS created FROM existing
                        UNION ALL
                        SELECT person_id, NULL::text, TRUE AS created FROM new_identity
                        """,
                        addr, display, json.dumps(evidence, default=str),
                    )
                    if row and row["created"]:
                        persons_created += 1
                    elif row and name_hint and _is_email_derived_name(row["old_name"], addr):
                        await conn.execute(
                            "UPDATE canonical.person SET display_name = $2 WHERE id = $1::uuid",
                            row["person_id"], name_hint,
                        )
                        names_upgraded += 1

    log.info(
        "google_contacts → canonical: emails_seen=%d created=%d names_upgraded=%d",
        seen, persons_created, names_upgraded,
    )
    return seen, persons_created, names_upgraded


async def sync_photos_from_google_contacts(pool: asyncpg.Pool) -> int:
    """For every raw.google_contact with a non-default photo and at least one
    email identity, upsert memory.person_photo with the CDN URL. People API
    photo URLs are stable lh3.googleusercontent.com endpoints — no need to
    download the bytes.

    Default Google silhouette photos have `default: true` in the payload;
    we skip those so the UI falls through to gravatar / initials.

    Returns the number of rows written (idempotent — re-runs that find the
    same URL still update fetched_at).
    """
    async with pool.acquire() as conn:
        # photos[] preferring the primary non-default entry. We avoid jsonpath
        # because the payload shape from otherContacts is shallow and a
        # simple WHERE-EXISTS scan is the same cost at our row count.
        rows = await conn.fetch(
            """
            SELECT emails, payload->'photos' AS photos
              FROM raw.google_contact
             WHERE array_length(emails, 1) > 0
               AND jsonb_typeof(payload->'photos') = 'array'
               AND jsonb_array_length(payload->'photos') > 0
            """
        )

    written = 0
    for r in rows:
        # asyncpg returns jsonb as a string in this pool (no codec
        # registered) — parse it to iterate the array.
        raw_photos = r["photos"]
        try:
            photos = json.loads(raw_photos) if isinstance(raw_photos, str) else raw_photos
        except (json.JSONDecodeError, TypeError):
            continue

        # Pick the first non-default photo URL — Google sometimes returns the
        # default silhouette as a second entry when the user has a real one.
        chosen_url: str | None = None
        for ph in photos or []:
            if not isinstance(ph, dict):
                continue
            if ph.get("default"):
                continue
            url = ph.get("url")
            if url:
                chosen_url = url
                break
        if not chosen_url:
            continue

        # Find the canonical person via any of this contact's emails.
        for addr in r["emails"]:
            addr = (addr or "").lower().strip()
            if not addr or "@" not in addr:
                continue
            async with pool.acquire() as conn:
                async with conn.transaction():
                    pid_row = await conn.fetchrow(
                        """
                        SELECT person_id::text AS pid
                          FROM canonical.identity
                         WHERE source = 'email' AND source_id = $1
                         LIMIT 1
                        """,
                        addr,
                    )
                    if not pid_row:
                        continue
                    # Don't overwrite a telegram-downloaded local file (better
                    # than a third-party URL that may rotate). If a telegram
                    # row already exists for this person, leave it.
                    await conn.execute(
                        """
                        INSERT INTO memory.person_photo
                          (person_id, source, url, fetched_at)
                        VALUES ($1::uuid, 'google_contacts', $2, now())
                        ON CONFLICT (person_id) DO UPDATE SET
                          url = EXCLUDED.url, fetched_at = now()
                          WHERE memory.person_photo.source != 'telegram'
                        """,
                        pid_row["pid"], chosen_url,
                    )
                    written += 1
                    break  # one upsert per contact

    log.info("google_contacts → memory.person_photo: written=%d", written)
    return written


def _is_email_derived_name(existing: str | None, email: str) -> bool:
    """True if the existing display_name looks like our own placeholder
    (email local-part Title-Cased or 'Telegram user N')."""
    if not existing:
        return True
    s = existing.strip()
    if not s:
        return True
    if s.lower().startswith("telegram user "):
        return True
    return s == _email_to_display_name(email, None)


async def sync_interactions_email(pool: asyncpg.Pool, cfg: Config) -> tuple[int, int]:
    """Pass 2 (gmail): insert one canonical.interaction per unprocessed
    raw.gmail_message. Returns (rows_seen, rows_inserted)."""
    async with pool.acquire() as conn:
        own = await _own_emails(conn)
        person_map = await _email_identity_map(conn)
    log.info(
        "email normalizer: %d own accounts, %d email identities resolved",
        len(own), len(person_map),
    )

    seen = inserted = orphaned = 0
    while True:
        async with pool.acquire() as conn:
            batch = await conn.fetch(
                """
                SELECT r.id, r.account_email, r.from_address, r.to_addresses,
                       r.cc_addresses, r.subject, r.body_text, r.body_html,
                       r.internal_date
                  FROM raw.gmail_message r
             LEFT JOIN canonical.interaction c
                    ON c.raw_source = 'raw.gmail_message' AND c.raw_id = r.id
                 WHERE c.id IS NULL
              ORDER BY r.id
                 LIMIT $1
                """,
                cfg.batch_size,
            )
        if not batch:
            break

        records: list[tuple[Any, ...]] = []
        for r in batch:
            seen += 1
            from_addr = (r["from_address"] or "").lower() or None
            tos = [t.lower() for t in (r["to_addresses"] or []) if t]
            ccs = [t.lower() for t in (r["cc_addresses"] or []) if t]
            account = (r["account_email"] or "").lower()

            outbound = bool(from_addr and from_addr in own)
            direction = "outbound" if outbound else "inbound"

            if outbound:
                # Pick the first recipient that isn't one of our own emails
                # and IS in the person_map (we don't want to drop the row
                # just because we haven't seen a recipient before; we run
                # sync_persons_email first to populate, but messages with
                # no resolvable counterparty get person_id=NULL).
                counterparty = next(
                    (a for a in (tos + ccs)
                     if a and a not in own and a in person_map),
                    None,
                )
                if counterparty is None:
                    # Fallback: any non-own recipient even if not yet a person
                    counterparty = next(
                        (a for a in (tos + ccs) if a and a not in own),
                        None,
                    )
            else:
                counterparty = from_addr if from_addr and from_addr not in own else None

            person_id = person_map.get(counterparty) if counterparty else None
            if person_id is None:
                orphaned += 1

            body = _build_body(r["subject"], r["body_text"], r["body_html"])

            records.append((
                person_id, "gmail", direction, r["internal_date"], body,
                "raw.gmail_message", r["id"],
            ))

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(
                    """
                    INSERT INTO canonical.interaction
                      (person_id, channel, direction, occurred_at, body,
                       raw_source, raw_id)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (raw_source, raw_id) DO NOTHING
                    """,
                    records,
                )
        inserted += len(batch)
        log.info("email batch: %d rows; seen=%d inserted=%d orphaned=%d",
                 len(batch), seen, inserted, orphaned)

    log.info("sync_interactions_email: seen=%d inserted=%d orphaned=%d",
             seen, inserted, orphaned)
    return seen, inserted
