#!/usr/bin/env python3
"""Backfill company `domain` + `country` for the curated company entities.

The factual sources are thin (only ~1/408 companies have a work-email domain),
so this leans on an LLM lookup — but conservatively: the model is told to
return null rather than guess, and never to fabricate a domain. Wrong/blank
domains are cheap (the favicon UI falls back to initials); countries are
constrained to the vocabulary the UI's countryFlag() recognises so a stored
value actually renders a flag.

Idempotent: only fills columns that are currently empty (manual edits and
prior fills are preserved). Run on the droplet (DB + ANTHROPIC_API_KEY there):

    /srv/memory/apps/merge_api/.venv/bin/python scripts/enrich_companies.py [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys

import asyncpg

MODEL = os.environ.get("ENRICH_MODEL", "claude-haiku-4-5")
BATCH = int(os.environ.get("ENRICH_BATCH", "20"))

# Country names the UI's countryFlag() can turn into a flag. Steering the model
# to these keeps stored countries flag-renderable.
COUNTRIES = [
    "United States", "United Kingdom", "United Arab Emirates", "Japan", "South Korea",
    "China", "Hong Kong", "Taiwan", "Singapore", "Cayman Islands", "British Virgin Islands",
    "Russia", "Germany", "France", "Switzerland", "Netherlands", "Canada", "Australia",
    "India", "Thailand", "Vietnam", "Indonesia", "Israel", "Spain", "Italy", "Poland",
    "Ukraine", "Brazil", "Mexico", "Turkey", "Estonia", "Lithuania", "Portugal", "Ireland",
    "Sweden", "Finland", "Norway", "Denmark", "Austria", "Belgium", "Czech Republic",
    "Greece", "Malta", "Cyprus", "Luxembourg", "Seychelles", "Panama", "Philippines",
    "Malaysia", "Saudi Arabia", "Qatar", "Bahrain", "Kazakhstan", "Georgia", "Armenia",
    "Argentina", "Nigeria", "South Africa", "Egypt", "New Zealand", "Gibraltar",
    "Liechtenstein", "Bermuda",
]
_COUNTRY_SET = {c.lower() for c in COUNTRIES}

SYSTEM = (
    "You are a precise company-data lookup. For each company NAME you are given, "
    "return its official primary website domain and its headquarters country — but "
    "ONLY when you are confident the name refers to a specific, real, identifiable "
    "organization.\n"
    "Hard rules:\n"
    "- NEVER guess or fabricate a domain. If you are not sure of the real domain, use null.\n"
    "- If the name is generic, ambiguous, a person's name, a common word, or a "
    "token/protocol rather than a company you can identify, use null for both fields.\n"
    "- domain must be the bare host only: 'binance.com', not 'https://www.binance.com/'.\n"
    "- For country, prefer EXACTLY one of these names when applicable: "
    + ", ".join(COUNTRIES) + ". If unknown, use null.\n"
    "Output ONLY a JSON array, one object per input company IN THE SAME ORDER, each "
    '{"name": <echo>, "domain": <string|null>, "country": <string|null>}. No prose.'
)

_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9-]+)+$")


def _clean_domain(v) -> str | None:
    if not isinstance(v, str):
        return None
    d = v.strip().lower().split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    if d.startswith("www."):
        d = d[4:]
    if not d or " " in d or "." not in d or not _DOMAIN_RE.match(d):
        return None
    return d


def _clean_country(v) -> str | None:
    if not isinstance(v, str):
        return None
    c = v.strip()
    # Only keep flag-renderable countries (else it's noise with no UI payoff).
    return c if c.lower() in _COUNTRY_SET else None


def _strip_fences(t: str) -> str:
    t = t.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


async def enrich_batch(client, rows: list[dict]) -> dict[str, dict]:
    payload = json.dumps([{"name": r["name"]} for r in rows], ensure_ascii=False)
    resp = await client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM,
        messages=[{"role": "user", "content": payload}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    try:
        arr = json.loads(_strip_fences(text))
    except Exception:
        print(f"  ! could not parse LLM output for batch: {text[:160]!r}", file=sys.stderr)
        return {}
    out: dict[str, dict] = {}
    for item in arr if isinstance(arr, list) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            out[item["name"].strip().lower()] = item
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="cap companies processed (0=all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import anthropic

    db_url = (
        os.environ.get("MERGE_API_DATABASE_URL")
        or f"postgres://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ.get('POSTGRES_HOST','127.0.0.1')}:{os.environ.get('POSTGRES_PORT','5432')}"
        f"/{os.environ['POSTGRES_DB']}"
    )
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, statement_cache_size=0)
    filled_d = filled_c = seen = 0
    try:
        async with pool.acquire() as conn:
            rows = [
                dict(r)
                for r in await conn.fetch(
                    """
                    SELECT id::text, name FROM memory.company
                     WHERE deleted_at IS NULL
                       AND ((domain IS NULL OR domain = '') OR (country IS NULL OR country = ''))
                     ORDER BY (SELECT count(*) FROM memory.company_person cp WHERE cp.company_id = company.id) DESC,
                              name
                    """
                )
            ]
        if args.limit:
            rows = rows[: args.limit]
        print(f"{len(rows)} companies need enrichment (model={MODEL}, batch={BATCH}, dry_run={args.dry_run})")

        for i in range(0, len(rows), BATCH):
            batch = rows[i : i + BATCH]
            got = await enrich_batch(client, batch)
            updates = []
            for r in batch:
                seen += 1
                item = got.get(r["name"].strip().lower(), {})
                dom = _clean_domain(item.get("domain"))
                cty = _clean_country(item.get("country"))
                if dom or cty:
                    updates.append((r["id"], dom, cty, r["name"]))
            for cid, dom, cty, name in updates:
                if dom:
                    filled_d += 1
                if cty:
                    filled_c += 1
                tag = " ".join(filter(None, [dom and f"domain={dom}", cty and f"country={cty}"]))
                print(f"  · {name[:34]:<34} {tag}")
                if not args.dry_run:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            """
                            UPDATE memory.company
                               SET domain  = CASE WHEN (domain IS NULL OR domain='')   AND $2 <> '' THEN $2 ELSE domain END,
                                   country = CASE WHEN (country IS NULL OR country='') AND $3 <> '' THEN $3 ELSE country END,
                                   updated_at = now()
                             WHERE id = $1::uuid AND deleted_at IS NULL
                            """,
                            cid, dom or "", cty or "",
                        )
            print(f"  [batch {i//BATCH+1}] {seen}/{len(rows)} seen · {filled_d} domains · {filled_c} countries")
    finally:
        await pool.close()
    print(f"done: {filled_d} domains + {filled_c} countries filled across {seen} companies")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
