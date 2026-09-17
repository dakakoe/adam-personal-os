#!/usr/bin/env python3
"""Create company entities (and person↔company links) from the company names
already sitting in people's LinkedIn data, and seed person.location.

Deterministic — no LLM. It reads what's there:
  * raw.linkedin_profile_capture: current_company (+ company_url, is_current)
    and every experience[] entry (past employers, is_current=false), joined to
    the person via their 'linkedin' identity (vanity).
  * raw.linkedin_connection: company + position, joined via the vanity in url.
  * canonical.profile.structured -> current_company (the LLM profile), the
    original seed source, kept so nothing regresses.
Location is seeded from a capture's location, else an imported contact's.

Dedup is by norm_name = lower(btrim(name)), matching memory.company. A company
is created once; company_url fills memory.company.linkedin_url when empty. A
person↔company link is upserted with is_current true if ANY source says so.

Idempotent: re-running only adds what's missing and never overwrites a manual
edit (person.location is filled only when NULL; a company's linkedin_url only
when empty).

Run on the droplet (DB there):
    /srv/memory/apps/merge_api/.venv/bin/python scripts/backfill_companies.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

# Not companies — placeholders LinkedIn/exports leave in the employer field.
# Lowercased for comparison against norm_name.
_JUNK = {
    "", "-", "—", "n/a", "na", "none", "self", "self-employed", "self employed",
    "selfemployed", "freelance", "freelancer", "independent", "independent contractor",
    "unemployed", "student", "retired", "private", "confidential", "stealth",
    "stealth startup", "stealth mode", "various", "multiple",
}


def normalize_company_name(raw: str | None) -> str | None:
    """Cleaned display name, or None if the value isn't a real company.

    Collapses whitespace and strips a trailing legal suffix comma tail is left
    alone — 'Acme, Inc.' stays as written; only whitespace is normalized. Junk
    placeholders and one-character names are dropped."""
    if not raw:
        return None
    name = " ".join(raw.split()).strip()
    if len(name) < 2:
        return None
    if name.lower() in _JUNK:
        return None
    return name


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    dsn = (
        os.environ.get("MERGE_API_DATABASE_URL")
        or f"postgres://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ.get('POSTGRES_HOST','127.0.0.1')}:{os.environ.get('POSTGRES_PORT','5432')}"
        f"/{os.environ['POSTGRES_DB']}"
    )
    conn = await asyncpg.connect(dsn)
    try:
        # (person_id, company, url, role, is_current) from every source.
        rows = await conn.fetch(
            """
            -- current employer, live capture (carries the company LinkedIn URL)
            SELECT i.person_id, c.current_company AS company, c.company_url AS url,
                   c.current_title AS role, TRUE AS is_current
              FROM raw.linkedin_profile_capture c
              JOIN canonical.identity i ON i.source = 'linkedin' AND i.source_id = c.vanity
             WHERE c.current_company IS NOT NULL
            UNION ALL
            -- past employers from the experience array
            SELECT i.person_id, e->>'company', NULL, e->>'title', FALSE
              FROM raw.linkedin_profile_capture c
              JOIN canonical.identity i ON i.source = 'linkedin' AND i.source_id = c.vanity
              CROSS JOIN LATERAL jsonb_array_elements(c.experience) e
             WHERE e->>'company' IS NOT NULL
            UNION ALL
            -- connections export
            SELECT i.person_id, lc.company, NULL, lc.position, TRUE
              FROM raw.linkedin_connection lc
              JOIN canonical.identity i ON i.source = 'linkedin'
                AND (lc.url = 'https://www.linkedin.com/in/' || i.source_id
                     OR lc.url LIKE '%linkedin.com/in/' || i.source_id || '%')
             WHERE lc.company IS NOT NULL
            UNION ALL
            -- the LLM profile's current company (original seed source)
            SELECT pr.person_id, pr.structured->>'current_company', NULL, NULL, TRUE
              FROM memory.profile pr
             WHERE pr.structured->>'current_company' IS NOT NULL
            """
        )

        # --- companies: norm_name -> {name, url} ---------------------------
        companies: dict[str, dict] = {}
        # person -> {norm -> {is_current, role}}
        links: dict[str, dict[str, dict]] = {}
        for r in rows:
            name = normalize_company_name(r["company"])
            if not name:
                continue
            norm = name.lower().strip()
            c = companies.setdefault(norm, {"name": name, "url": None})
            if not c["url"] and r["url"]:
                c["url"] = r["url"].strip() or None
            pl = links.setdefault(str(r["person_id"]), {})
            link = pl.setdefault(norm, {"is_current": False, "role": None})
            link["is_current"] = link["is_current"] or bool(r["is_current"])
            if not link["role"] and r["role"]:
                link["role"] = " ".join(r["role"].split()).strip() or None

        # --- locations: person -> best available -------------------------
        loc_rows = await conn.fetch(
            """
            SELECT i.person_id, c.location
              FROM raw.linkedin_profile_capture c
              JOIN canonical.identity i ON i.source = 'linkedin' AND i.source_id = c.vanity
             WHERE c.location IS NOT NULL AND btrim(c.location) <> ''
            UNION ALL
            SELECT i.person_id, g.location
              FROM raw.linkedin_imported_contact g
              JOIN canonical.identity i ON i.source = 'email'
                AND lower(i.source_id) = ANY (SELECT lower(x) FROM unnest(g.emails) x)
             WHERE g.location IS NOT NULL AND btrim(g.location) <> ''
            """
        )
        locations: dict[str, str] = {}
        for r in loc_rows:
            locations.setdefault(str(r["person_id"]), " ".join(r["location"].split()).strip())

        print(f"companies: {len(companies)}   links: {sum(len(v) for v in links.values())}   "
              f"locations: {len(locations)}")
        if args.dry_run:
            for norm, c in list(companies.items())[:15]:
                print(f"  would ensure company: {c['name']!r}"
                      + (f"  linkedin={c['url']}" if c["url"] else ""))
            return 0

        # --- write, one transaction --------------------------------------
        created = linked = located = 0
        async with conn.transaction():
            norm_to_id: dict[str, str] = {}
            for norm, c in companies.items():
                row = await conn.fetchrow(
                    "SELECT id, linkedin_url FROM memory.company "
                    " WHERE norm_name = $1 AND deleted_at IS NULL LIMIT 1", norm)
                if row is None:
                    row = await conn.fetchrow(
                        "INSERT INTO memory.company (name, norm_name, linkedin_url) "
                        "VALUES ($1, $2, $3) RETURNING id, linkedin_url",
                        c["name"], norm, c["url"])
                    created += 1
                elif c["url"] and not row["linkedin_url"]:
                    await conn.execute(
                        "UPDATE memory.company SET linkedin_url = $2 WHERE id = $1",
                        row["id"], c["url"])
                norm_to_id[norm] = row["id"]

            for person_id, pl in links.items():
                for norm, link in pl.items():
                    cid = norm_to_id.get(norm)
                    if not cid:
                        continue
                    # is_current wins on conflict; role fills if we have one.
                    await conn.execute(
                        """
                        INSERT INTO memory.company_person (company_id, person_id, role, is_current)
                        VALUES ($1, $2::uuid, $3, $4)
                        ON CONFLICT (company_id, person_id) DO UPDATE
                          SET is_current = memory.company_person.is_current OR EXCLUDED.is_current,
                              role = COALESCE(memory.company_person.role, EXCLUDED.role)
                        """,
                        cid, person_id, link["role"], link["is_current"])
                    linked += 1

            for person_id, loc in locations.items():
                r = await conn.execute(
                    "UPDATE canonical.person SET location = $2 "
                    " WHERE id = $1::uuid AND location IS NULL AND deleted_at IS NULL",
                    person_id, loc)
                if r.endswith(" 1"):
                    located += 1

        print(f"created {created} companies, {linked} links, set {located} locations")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
