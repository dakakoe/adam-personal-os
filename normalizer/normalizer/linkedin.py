"""LinkedIn-side normalization: raw.linkedin_* → canonical layer.

Three sources fold into canonical:

1. raw.linkedin_connection (your saved connections)
   → canonical.identity (source='email' if present, source='linkedin' for vanity)
   → canonical.person create-or-upgrade-name
   → memory.profile.structured fill-in (current_company, current_role) when LLM empty

2. raw.linkedin_message (DM history)
   → canonical.interaction (channel='linkedin', direction inferred from sender URL)

3. raw.linkedin_imported_contact (phone-book the user uploaded to LinkedIn)
   → canonical.identity for each email
   → memory.extracted_signal for each phone (source='linkedin_import')
   → name upgrades

Self URL is hard-coded based on the export; override via env if needed.
"""

from __future__ import annotations

import json
import logging
import re
import os
from typing import Any

import asyncpg

from .config import Config

log = logging.getLogger(__name__)


_LINKEDIN_VANITY_RE = re.compile(
    r"linkedin\.com/in/([A-Za-z0-9\-_%]+)/?", re.IGNORECASE
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_PHONE_DIGITS_RE = re.compile(r"\D+")


def _self_profile_url() -> str:
    """Defaults to the URL seen in the sample messages.csv; overridable."""
    return os.environ.get(
        "LINKEDIN_SELF_PROFILE_URL", ""  # set your own /in/ URL in .env
    ).rstrip("/").lower()


def _vanity_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = _LINKEDIN_VANITY_RE.search(url)
    return m.group(1).lower() if m else None


def _strip_html(html: str) -> str:
    text = _HTML_TAG_RE.sub(" ", html or "")
    return _WS_RE.sub(" ", text).strip()


def _normalize_phone(p: str | None) -> str | None:
    if not p:
        return None
    digits = _PHONE_DIGITS_RE.sub("", p)
    return digits or None


def _display_name_from(first: str | None, last: str | None,
                       fallback_handle: str | None) -> str:
    parts = [x for x in (first, last) if x]
    if parts:
        return " ".join(parts)
    if fallback_handle:
        return fallback_handle
    return "Unknown"


def _name_looks_synthetic(name: str | None) -> bool:
    """Heuristic shared with the vCard importer: empty, 'Telegram user N',
    or matches the email-local-part Title-Cased."""
    if not name:
        return True
    n = name.strip()
    if not n:
        return True
    return n.lower().startswith("telegram user ")


# --- pass 1: connections ----------------------------------------------------

async def sync_connections(pool: asyncpg.Pool) -> dict[str, int]:
    """Walk raw.linkedin_connection. For each connection:
      - vanity → canonical.identity (source='linkedin')
      - email (if present) → canonical.identity (source='email')
      - Either upsert creates canonical.person if none exists yet.
      - Push company/position to memory.profile.structured if LLM left them empty.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT first_name, last_name, email, company, position, url, connected_on "
            "FROM raw.linkedin_connection"
        )

    seen = persons_created = names_upgraded = profile_overlays = 0
    for r in rows:
        vanity = _vanity_from_url(r["url"])
        if not vanity:
            continue
        seen += 1
        display = _display_name_from(r["first_name"], r["last_name"], vanity)
        email = (r["email"] or "").lower().strip() or None
        evidence = {
            "first_seen_source": "linkedin_connection",
            "company": r["company"],
            "position": r["position"],
            "connected_on": r["connected_on"].isoformat() if r["connected_on"] else None,
        }

        async with pool.acquire() as conn:
            async with conn.transaction():
                # 1. choose target person: existing-linkedin > existing-email > new
                existing_li = await conn.fetchrow(
                    "SELECT person_id, (SELECT display_name FROM canonical.person WHERE id=person_id) AS old_name "
                    "FROM canonical.identity WHERE source='linkedin' AND source_id=$1 LIMIT 1",
                    vanity,
                )
                existing_em = None
                if email and "@" in email:
                    existing_em = await conn.fetchrow(
                        "SELECT person_id, (SELECT display_name FROM canonical.person WHERE id=person_id) AS old_name "
                        "FROM canonical.identity WHERE source='email' AND source_id=$1 LIMIT 1",
                        email,
                    )

                if existing_li:
                    pid = existing_li["person_id"]
                    old_name = existing_li["old_name"]
                    is_new = False
                elif existing_em:
                    pid = existing_em["person_id"]
                    old_name = existing_em["old_name"]
                    is_new = False
                else:
                    row = await conn.fetchrow(
                        "INSERT INTO canonical.person (display_name) VALUES ($1) RETURNING id",
                        display,
                    )
                    pid = row["id"]
                    old_name = None
                    is_new = True
                    persons_created += 1

                if not is_new and _name_looks_synthetic(old_name):
                    await conn.execute(
                        "UPDATE canonical.person SET display_name = $2 WHERE id = $1::uuid",
                        pid, display,
                    )
                    names_upgraded += 1

                # 2. attach linkedin identity (if not already)
                await conn.execute(
                    """
                    INSERT INTO canonical.identity (person_id, source, source_id, evidence)
                    VALUES ($1::uuid, 'linkedin', $2, $3::jsonb)
                    ON CONFLICT (source, source_id) DO NOTHING
                    """,
                    pid, vanity, json.dumps(evidence, default=str),
                )

                # 3. attach email identity (if not already) to same person
                if email and "@" in email:
                    await conn.execute(
                        """
                        INSERT INTO canonical.identity (person_id, source, source_id, evidence)
                        VALUES ($1::uuid, 'email', $2, $3::jsonb)
                        ON CONFLICT (source, source_id) DO NOTHING
                        """,
                        pid, email,
                        json.dumps({"first_seen_source": "linkedin_connection"}, default=str),
                    )

                # 3. overlay company/title into memory.profile.structured when LLM
                #    didn't fill them. Read-then-write keeps SQL trivial (avoids
                #    asyncpg's parameter-type inference issues around jsonb expr).
                if r["company"] or r["position"]:
                    struct_row = await conn.fetchrow(
                        "SELECT structured FROM memory.profile WHERE person_id = $1::uuid",
                        pid,
                    )
                    if struct_row and struct_row["structured"]:
                        existing = struct_row["structured"]
                        if isinstance(existing, str):
                            try:
                                existing = json.loads(existing)
                            except Exception:
                                existing = None
                        if isinstance(existing, dict):
                            mutated = False
                            if r["company"] and not existing.get("current_company"):
                                existing["current_company"] = r["company"]
                                mutated = True
                            if r["position"] and not existing.get("current_role"):
                                existing["current_role"] = r["position"]
                                mutated = True
                            if mutated:
                                await conn.execute(
                                    "UPDATE memory.profile SET structured = $2::jsonb "
                                    "WHERE person_id = $1::uuid",
                                    pid, json.dumps(existing, default=str),
                                )
                                profile_overlays += 1

    log.info(
        "linkedin connections: seen=%d persons_created=%d names_upgraded=%d profile_overlays=%d",
        seen, persons_created, names_upgraded, profile_overlays,
    )
    return {"seen": seen, "created": persons_created,
            "names_upgraded": names_upgraded, "profile_overlays": profile_overlays}


# --- pass 2: messages -------------------------------------------------------

async def sync_messages(pool: asyncpg.Pool, cfg: Config) -> dict[str, int]:
    """Walk raw.linkedin_message → canonical.interaction (channel='linkedin').
    Counterparty resolution via SENDER PROFILE URL (or recipient if outbound).
    """
    self_url = _self_profile_url()
    self_vanity = _vanity_from_url(self_url)

    async with pool.acquire() as conn:
        # Build a vanity → person_id map for fast resolution
        ident_map_rows = await conn.fetch(
            "SELECT i.source_id, COALESCE(p.merged_into, p.id)::text AS person_id "
            "FROM canonical.identity i "
            "JOIN canonical.person p ON p.id = i.person_id "
            "WHERE i.source = 'linkedin'"
        )
        vanity_to_pid = {r["source_id"]: r["person_id"] for r in ident_map_rows}

    inserted = orphaned = seen = 0
    while True:
        async with pool.acquire() as conn:
            batch = await conn.fetch(
                """
                SELECT r.id, r.from_profile_url, r.to_profile_urls,
                       r.message_date, r.subject, r.content, r.folder
                  FROM raw.linkedin_message r
             LEFT JOIN canonical.interaction c
                    ON c.raw_source = 'raw.linkedin_message' AND c.raw_id = r.id
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
            sender_vanity = _vanity_from_url(r["from_profile_url"])
            outbound = sender_vanity == self_vanity

            # Counterparty: the OTHER side of the message
            if outbound:
                cp_vanity = None
                for url in (r["to_profile_urls"] or []):
                    v = _vanity_from_url(url)
                    if v and v != self_vanity:
                        cp_vanity = v
                        break
            else:
                cp_vanity = sender_vanity

            pid = vanity_to_pid.get(cp_vanity) if cp_vanity else None
            if pid is None:
                orphaned += 1

            body = _strip_html(r["content"] or "")
            if r["subject"]:
                body = f"Subject: {r['subject']}\n\n{body}".strip()

            records.append((
                pid, "linkedin", "outbound" if outbound else "inbound",
                r["message_date"], body or None,
                "raw.linkedin_message", r["id"],
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
        log.info("linkedin msg batch: %d rows; seen=%d inserted=%d orphaned=%d",
                 len(batch), seen, inserted, orphaned)

    log.info("linkedin messages: seen=%d inserted=%d orphaned=%d",
             seen, inserted, orphaned)
    return {"seen": seen, "inserted": inserted, "orphaned": orphaned}


# --- pass 3: imported contacts ---------------------------------------------

async def sync_imported_contacts(pool: asyncpg.Pool) -> dict[str, int]:
    """Walk raw.linkedin_imported_contact. For each row:
      - each email → canonical.identity (source='email') + person create/name upgrade
      - each phone → memory.extracted_signal (signal_type='phone', source='linkedin_import')
        — separate phone-match merger can later use these to cross-link with telegram
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT first_name, last_name, nickname, title, emails, phones, location "
            "FROM raw.linkedin_imported_contact"
        )

    seen_emails = persons_created = names_upgraded = 0
    phones_recorded = 0
    for r in rows:
        display = _display_name_from(
            r["first_name"], r["last_name"],
            r["nickname"] or (r["emails"][0] if r["emails"] else None),
        )
        evidence = {
            "first_seen_source": "linkedin_imported_contact",
            "title": r["title"], "location": r["location"],
        }

        anchor_pid: str | None = None
        for addr in (r["emails"] or []):
            addr = (addr or "").lower().strip()
            if not addr or "@" not in addr:
                continue
            seen_emails += 1
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
                    elif row and _name_looks_synthetic(row["old_name"]):
                        await conn.execute(
                            "UPDATE canonical.person SET display_name = $2 WHERE id = $1::uuid",
                            row["person_id"], display,
                        )
                        names_upgraded += 1
                    if row:
                        anchor_pid = row["person_id"]

        # Phones attach to the anchor person (typically the first email's person).
        # If no email match was possible, the phone is orphaned for now;
        # subsequent phone-match merger pass will assemble these into persons.
        if anchor_pid:
            for phone in (r["phones"] or []):
                normalized = _normalize_phone(phone)
                if not normalized:
                    continue
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO memory.extracted_signal
                          (person_id, signal_type, value, confidence, source, evidence)
                        VALUES ($1::uuid, 'phone', $2, 'high', 'linkedin_import', $3::jsonb)
                        ON CONFLICT (person_id, signal_type, value, source) DO UPDATE
                          SET last_seen_at = now()
                        """,
                        anchor_pid, normalized,
                        json.dumps({"raw": phone, "name": display}, default=str),
                    )
                phones_recorded += 1

    log.info(
        "linkedin imported_contacts: emails=%d persons_created=%d names_upgraded=%d phones=%d",
        seen_emails, persons_created, names_upgraded, phones_recorded,
    )
    return {"emails": seen_emails, "created": persons_created,
            "names_upgraded": names_upgraded, "phones_recorded": phones_recorded}
