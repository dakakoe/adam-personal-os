"""Google Contacts vCard importer.

Google Takeout's All Contacts.vcf is a stream of minimal vCards — usually
just EMAIL + FN (full name) per contact, sometimes with N (structured
name). We pull the email + display_name into canonical.identity /
canonical.person directly, no raw layer required (the vCard format is its
own raw form already).

Strategy:
  - For each EMAIL in a card, ensure canonical.identity (source='email').
  - If no such identity yet, create a new canonical.person using the FN
    (or derive from the email local-part).
  - If the identity exists AND the existing person has a worse name
    (synthetic / email-derived), upgrade display_name to the vCard's FN.

Idempotent: re-running on the same file is a no-op.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterator

import asyncpg

log = logging.getLogger(__name__)

_FOLD_RE = re.compile(r"\r?\n[ \t]")  # vCard line folding


def parse_vcards(text: str) -> list[dict[str, Any]]:
    """Walk an .vcf file and yield one dict per contact: {emails, fn, n}.
    Uses simple line-based parsing — sufficient for Google Takeout output."""
    # Un-fold continuation lines first.
    text = _FOLD_RE.sub("", text)
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("BEGIN:VCARD"):
            cur = {"emails": [], "fn": None, "n": None}
            continue
        if upper.startswith("END:VCARD"):
            if cur:
                out.append(cur)
            cur = None
            continue
        if cur is None:
            continue

        # Property line: NAME[;PARAMS]:VALUE  — name may have a prefix like
        # "item1.EMAIL". Strip the prefix.
        if ":" not in line:
            continue
        prop_part, value = line.split(":", 1)
        prop = prop_part.split(";", 1)[0]
        base = prop.split(".", 1)[-1].upper()
        if base == "EMAIL":
            addr = value.strip().lower()
            if addr and "@" in addr and addr not in cur["emails"]:
                cur["emails"].append(addr)
        elif base == "FN" and not cur["fn"]:
            cur["fn"] = value.strip() or None
        elif base == "N" and not cur["n"]:
            cur["n"] = value.strip() or None

    return out


def _derive_display_name(email: str, fn: str | None) -> str:
    """Best-effort name. Prefer the vCard's FN; else derive from the email
    local-part by Title-Casing tokens."""
    if fn:
        return fn
    local = email.split("@", 1)[0]
    return " ".join(p.capitalize() for p in re.split(r"[._\-]+", local) if p) or email


def _name_looks_synthetic(name: str | None, email: str | None = None) -> bool:
    """Is the existing person name a placeholder we should overwrite when a
    vCard offers a better one?

    Flags:
      - empty / None
      - 'Telegram user 12345' synthetics
      - names we ourselves derived from the email local-part
        (e.g. 'A Rarible' for a@rarible.com, 'Sm' for sm@x.com)
    """
    if not name:
        return True
    n = name.strip()
    if not n:
        return True
    low = n.lower()
    if low.startswith("telegram user "):
        return True
    if email:
        derived = _derive_display_name(email, None)
        if derived and n == derived:
            return True
    return False


async def import_vcards_from_file(pool: asyncpg.Pool, vcf_path: str) -> dict[str, int]:
    text = Path(vcf_path).read_text(encoding="utf-8", errors="replace")
    cards = parse_vcards(text)
    log.info("vcard %s: %d cards parsed", vcf_path, len(cards))

    seen = identities_added = persons_created = names_upgraded = 0
    for card in cards:
        emails = card["emails"]
        fn = card["fn"]
        for addr in emails:
            seen += 1
            display = _derive_display_name(addr, fn)
            evidence = {"first_seen_source": "google_contacts_vcard", "vcard_fn": fn}
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
                        identities_added += 1
                    elif row and fn and _name_looks_synthetic(row["old_name"], addr):
                        await conn.execute(
                            "UPDATE canonical.person SET display_name = $2 WHERE id = $1::uuid",
                            row["person_id"], fn,
                        )
                        names_upgraded += 1

    log.info(
        "vcard %s done: emails_seen=%d persons_created=%d names_upgraded=%d",
        vcf_path, seen, persons_created, names_upgraded,
    )
    return {
        "seen": seen,
        "persons_created": persons_created,
        "names_upgraded": names_upgraded,
    }


async def run(db_url: str, vcf_paths: list[str]) -> None:
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, statement_cache_size=0)
    try:
        for p in vcf_paths:
            await import_vcards_from_file(pool, p)
    finally:
        await pool.close()
