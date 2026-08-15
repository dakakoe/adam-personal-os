"""Merge-candidate generators.

Each generator looks at a different signal and writes pending rows into
memory.merge_candidate. The merge UI reads from that table for human review.

Generators are conservative — they emit suggestions, not auto-merges. The
existing identity_merger.run() handles the very-high-confidence auto path
(LLM-verified emails + name compatibility); anything weaker becomes a
candidate here.

Sources:
  llm_email   — pairs found by structured.personal_emails matching
                canonical.identity(source='email') across persons, where
                name-compat check fails (so auto-merger skipped them)
  fuzzy_name  — same first-token-prefix (Cyrillic-safe) + edit-distance ≤ 2,
                and BOTH persons have ≥1 interaction (active relationships)
  phone_match — same normalized phone in raw.telegram_user AND in
                memory.extracted_signal(source='linkedin_import', type='phone').
                Strong signal for telegram↔linkedin merge.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import asyncpg

from .config import Config
from .identity_merger import _names_compatible, _first_token, _levenshtein

log = logging.getLogger(__name__)


async def _upsert_candidate(
    conn: asyncpg.Connection,
    *,
    left_id: str,
    right_id: str,
    source: str,
    confidence: str,
    score: float | None,
    evidence: dict[str, Any],
) -> bool:
    """Insert a pending candidate; on conflict (same pair already known),
    do nothing — the first generator that flags a pair wins, and a human
    decision is what closes it out. Returns True if a new row was added."""
    # Canonicalize pair order so (A,B) and (B,A) collapse to one row via
    # the unique constraint on (LEAST, GREATEST).
    a, b = sorted([left_id, right_id])
    # ON CONFLICT targets the expression index by repeating its expression
    # list (postgres can't reference an index by name in this clause; it
    # needs the column/expression set so it can choose the matching index).
    row = await conn.fetchrow(
        """
        INSERT INTO memory.merge_candidate
          (left_person_id, right_person_id, source, confidence, score, evidence)
        VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb)
        ON CONFLICT (LEAST(left_person_id, right_person_id),
                     GREATEST(left_person_id, right_person_id))
        DO NOTHING
        RETURNING id
        """,
        a, b, source, confidence, score, json.dumps(evidence, default=str),
    )
    return row is not None


# --- generator: llm_email -----------------------------------------------

async def generate_llm_email_candidates(pool: asyncpg.Pool, self_emails: set[str]) -> int:
    """LLM-verified personal_email pairs that the auto-merger didn't take
    (usually because names didn't pass compatibility). Queue them for human
    judgement."""
    async with pool.acquire() as conn:
        own_account = await conn.fetch("SELECT email FROM raw.gmail_account")
        exclude = set(self_emails) | {r["email"].lower() for r in own_account}

        rows = await conn.fetch(
            """
            WITH personal_emails AS (
              SELECT mp.person_id AS tg_person_id, lower(e.value) AS email_addr
                FROM memory.profile mp
                CROSS JOIN LATERAL jsonb_array_elements_text(
                  COALESCE(mp.structured->'personal_emails', '[]'::jsonb)
                ) AS e(value)
               WHERE mp.structured IS NOT NULL
            )
            SELECT
              pe.tg_person_id::text  AS tg_person_id,
              tg_person.display_name AS tg_display_name,
              pe.email_addr          AS email_addr,
              ei.person_id::text     AS email_person_id,
              em_person.display_name AS em_display_name
            FROM personal_emails pe
            JOIN canonical.person tg_person   ON tg_person.id = pe.tg_person_id
            JOIN canonical.identity ei        ON ei.source='email' AND ei.source_id = pe.email_addr
            JOIN canonical.person em_person   ON em_person.id = ei.person_id
            WHERE tg_person.merged_into IS NULL
              AND em_person.merged_into IS NULL
              AND tg_person.id <> em_person.id
              AND NOT (pe.email_addr = ANY($1::text[]))
            """,
            list(exclude),
        )

    added = 0
    for r in rows:
        # The auto-merger already took the name-compatible ones. Only queue
        # the name-INCOMPATIBLE pairs (those need human judgement).
        if _names_compatible(r["tg_display_name"], r["em_display_name"]):
            continue
        evidence = {
            "matched_email": r["email_addr"],
            "tg_name": r["tg_display_name"],
            "em_name": r["em_display_name"],
            "rationale": "LLM tagged email as personal but names diverge — human check",
        }
        async with pool.acquire() as conn:
            ok = await _upsert_candidate(
                conn,
                left_id=r["tg_person_id"], right_id=r["email_person_id"],
                source="llm_email", confidence="medium", score=0.6,
                evidence=evidence,
            )
            if ok:
                added += 1
    log.info("candidates llm_email: %d new", added)
    return added


# --- generator: fuzzy_name ----------------------------------------------

async def generate_fuzzy_name_candidates(pool: asyncpg.Pool, *, limit: int = 1000) -> int:
    """Pairs of active persons whose first tokens match by prefix or short
    edit distance. Capped to top-N by combined interaction count to keep the
    queue manageable on first pass. Skips pairs that own *different*
    telegram or linkedin identities — those are two distinct
    single-account-channel users (e.g. @Sockol vs @easy2do) who happen
    to share a first name; they are not the same person."""
    async with pool.acquire() as conn:
        # Pull persons with interaction count for prioritization
        rows = await conn.fetch(
            """
            SELECT p.id::text AS person_id, p.display_name,
                   (SELECT count(*) FROM canonical.interaction WHERE person_id = p.id) AS msgs
              FROM canonical.person p
             WHERE p.merged_into IS NULL
               AND p.display_name IS NOT NULL
            """
        )
        # Build a single-account-channel index per person for the
        # incompatibility check below. Same logic as the merge_api's
        # INCOMPATIBLE_IDENTITY_EXISTS — keep the two in sync if you
        # extend one of them.
        sac_rows = await conn.fetch(
            """
            SELECT person_id::text AS pid, source, source_id
              FROM canonical.identity
             WHERE source IN ('telegram', 'linkedin')
            """
        )
    # person_id -> source -> set of source_ids (sets handle the rare
    # case where one person owns multiple telegram accounts pre-merge)
    sac: dict[str, dict[str, set[str]]] = {}
    for r in sac_rows:
        sac.setdefault(r["pid"], {}).setdefault(r["source"], set()).add(r["source_id"])

    def incompatible(a_id: str, b_id: str) -> bool:
        """Two persons are deterministically different if they both own
        a single-account channel and the source_ids don't overlap."""
        a = sac.get(a_id, {})
        b = sac.get(b_id, {})
        for src in ("telegram", "linkedin"):
            if src in a and src in b and a[src].isdisjoint(b[src]):
                return True
        return False

    def weak_name_shape(a_name: str, b_name: str) -> bool:
        """True when display_names don't satisfy the 2-token rule: both
        sides must have a second token, and the second tokens must match
        exactly or one must be a prefix of the other (handles 'Daniel A.'
        ↔ 'Daniel Adams' but rejects 'Dan' ↔ 'Daniel Adams' and 'Daniel
        Slupskiy' ↔ 'Daniel Haudenschild'). Mirrors merge_api.queries.
        WEAK_FUZZY_NAME_SHAPE — keep in sync if you change one."""
        a_parts = a_name.split() if a_name else []
        b_parts = b_name.split() if b_name else []
        if len(a_parts) < 2 or len(b_parts) < 2:
            return True
        a2 = a_parts[1].rstrip(".").lower()
        b2 = b_parts[1].rstrip(".").lower()
        if not a2 or not b2:
            return True
        if a2 == b2 or a2.startswith(b2) or b2.startswith(a2):
            return False
        return True

    # Group by first-token-prefix (3-char prefix) for O(n) candidate generation
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        tok = _first_token(r["display_name"])
        if len(tok) < 3:
            continue
        key = tok[:3]
        buckets.setdefault(key, []).append({
            "id": r["person_id"], "name": r["display_name"],
            "tok": tok, "msgs": r["msgs"],
        })

    # Within each bucket, pair persons whose first tokens are compatible
    pairs: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        for i, a in enumerate(bucket):
            for b in bucket[i + 1:]:
                if a["tok"] == b["tok"]:
                    score = 1.0
                elif a["tok"].startswith(b["tok"]) or b["tok"].startswith(a["tok"]):
                    score = 0.85
                else:
                    d = _levenshtein(a["tok"], b["tok"], cap=2)
                    if d > 2:
                        continue
                    score = 0.7 - 0.1 * d
                pairs.append((a, b, score))

    # Prioritize by combined interaction volume — busy contacts first
    pairs.sort(key=lambda p: (p[0]["msgs"] + p[1]["msgs"], p[2]), reverse=True)
    pairs = pairs[:limit]

    added = 0
    skipped_incompatible = 0
    skipped_weak_shape = 0
    for a, b, score in pairs:
        # Skip if either has zero interactions (very low value)
        if a["msgs"] == 0 and b["msgs"] == 0:
            continue
        # Skip if a deterministic channel says they're different people
        # (different @telegram or different linkedin vanity).
        if incompatible(a["id"], b["id"]):
            skipped_incompatible += 1
            continue
        # Skip if names don't pass the 2-token shape rule — "Dan" vs
        # "Daniel Adams" or "Daniel Slupskiy" vs "Daniel Haudenschild"
        # are noise the bucket-by-prefix approach would otherwise emit.
        if weak_name_shape(a["name"], b["name"]):
            skipped_weak_shape += 1
            continue
        evidence = {
            "left_name": a["name"], "right_name": b["name"],
            "first_token_left": a["tok"], "first_token_right": b["tok"],
            "left_messages": a["msgs"], "right_messages": b["msgs"],
        }
        confidence = "medium" if score >= 0.85 else "low"
        async with pool.acquire() as conn:
            ok = await _upsert_candidate(
                conn,
                left_id=a["id"], right_id=b["id"],
                source="fuzzy_name", confidence=confidence, score=score,
                evidence=evidence,
            )
            if ok:
                added += 1
    log.info(
        "candidates fuzzy_name: %d new (from %d pairs evaluated; "
        "%d skipped incompatible single-account; %d skipped weak name shape)",
        added, len(pairs), skipped_incompatible, skipped_weak_shape,
    )
    return added


# --- generator: phone_match ---------------------------------------------

_PHONE_DIGITS_RE = re.compile(r"\D+")


def _norm_phone(p: str | None) -> str | None:
    if not p:
        return None
    return _PHONE_DIGITS_RE.sub("", p) or None


async def generate_phone_match_candidates(pool: asyncpg.Pool) -> int:
    """Cross-channel phone match: telegram phone ↔ linkedin imported_contact
    phone. Strong signal because phones are nearly unique to a person."""
    async with pool.acquire() as conn:
        # All telegram phones with their canonical person
        tg = await conn.fetch(
            """
            SELECT u.phone AS phone,
                   COALESCE(p.merged_into, p.id)::text AS person_id,
                   p.display_name
              FROM raw.telegram_user u
              JOIN canonical.identity i ON i.source='telegram' AND i.source_id::bigint = u.source_user_id
              JOIN canonical.person p   ON p.id = i.person_id
             WHERE u.phone IS NOT NULL AND u.phone <> ''
               AND p.merged_into IS NULL
            """
        )
        # All extracted_signal phones from linkedin import with their canonical person
        li = await conn.fetch(
            """
            SELECT s.value AS phone, s.person_id::text AS person_id, p.display_name
              FROM memory.extracted_signal s
              JOIN canonical.person p ON p.id = s.person_id
             WHERE s.signal_type='phone'
               AND s.source='linkedin_import'
               AND p.merged_into IS NULL
            """
        )

    # Index linkedin phones by normalized form
    li_by_phone: dict[str, list[dict[str, Any]]] = {}
    for r in li:
        n = _norm_phone(r["phone"])
        if n:
            li_by_phone.setdefault(n, []).append(dict(r))

    added = 0
    seen_pairs: set[tuple[str, str]] = set()
    for r in tg:
        tg_phone = _norm_phone(r["phone"])
        if not tg_phone:
            continue
        matches = li_by_phone.get(tg_phone, [])
        for m in matches:
            if m["person_id"] == r["person_id"]:
                continue
            pair_key = tuple(sorted([r["person_id"], m["person_id"]]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            evidence = {
                "matched_phone": tg_phone,
                "telegram_name": r["display_name"],
                "linkedin_name": m["display_name"],
            }
            async with pool.acquire() as conn:
                ok = await _upsert_candidate(
                    conn,
                    left_id=r["person_id"], right_id=m["person_id"],
                    source="phone_match", confidence="high", score=0.95,
                    evidence=evidence,
                )
                if ok:
                    added += 1
    log.info("candidates phone_match: %d new", added)
    return added


# --- orchestrator -------------------------------------------------------

async def run(cfg: Config) -> int:
    pool = await asyncpg.create_pool(
        cfg.db_url, min_size=1, max_size=2, statement_cache_size=0
    )
    try:
        log.info("generating merge candidates (self_emails=%d)", len(cfg.self_emails))
        a = await generate_llm_email_candidates(pool, set(cfg.self_emails))
        b = await generate_phone_match_candidates(pool)
        c = await generate_fuzzy_name_candidates(pool, limit=1000)

        async with pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT count(*) FROM memory.merge_candidate WHERE status = 'pending'"
            )
        log.info("merge_candidate totals: llm=%d phone=%d fuzzy=%d  pending_now=%d",
                 a, b, c, total)
        return 0
    finally:
        await pool.close()
