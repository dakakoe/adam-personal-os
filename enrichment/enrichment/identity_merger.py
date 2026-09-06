"""Cross-channel identity merger.

Uses memory.profile.structured.personal_emails (LLM-verified) as the bridge.
When the LLM has confidently said an email belongs to a specific contact,
and that email has its own canonical.identity (from Gmail ingestion), we
collapse the two canonical.person rows by setting one's merged_into to
the other.

Rules:
  - Telegram person wins when conflicts arise (Telegram identity is older,
    has bio, has profile). The email-derived person becomes merged_into the
    Telegram person.
  - We never touch already-merged rows (merged_into IS NOT NULL).
  - Idempotent: re-runs are no-ops once everything's merged.
  - Audit trail: each merge updates canonical.person.notes with a short
    rationale so the operator can reverse if needed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

from .config import Config

log = logging.getLogger(__name__)


def _first_token(name: str) -> str:
    """Lowercased first alphabetic word of a display_name. Cyrillic-safe.
    'Jane Smith' -> 'jane';  'Alex_M' -> 'alex';  'Telegram user 1234' -> 'telegram'."""
    import re
    if not name:
        return ""
    tokens = re.findall(r"[^\W\d_]+", name, flags=re.UNICODE)
    return tokens[0].lower() if tokens else ""


def _levenshtein(a: str, b: str, *, cap: int = 3) -> int:
    """Standard edit distance; early-exits when current row min exceeds cap.
    Good enough for short first-name comparisons."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > cap:
        return cap + 1
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * lb
        row_min = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
            if cur[j] < row_min:
                row_min = cur[j]
        if row_min > cap:
            return cap + 1
        prev = cur
    return prev[lb]


def _names_compatible(tg_name: str, em_name: str) -> bool:
    """Conservative first-name match. Allows prefix or short edit distance
    (Yulia/Ylia, Gordienko/Gordienco) but rejects unrelated names
    (the user/Omer, Farokh/Loxley)."""
    a = _first_token(tg_name)
    b = _first_token(em_name)
    if not a or not b:
        return False
    if a == b:
        return True
    if a.startswith(b) or b.startswith(a):
        # "Marc" prefix of "Marcus" — OK
        return min(len(a), len(b)) >= 3
    # Allow short edits to cover transliteration. Distance ≤ 2 with both
    # names ≥ 4 chars: "Yulia"/"Ylia" (1), "Gordienko"/"Gordienco" (1).
    if min(len(a), len(b)) >= 4 and _levenshtein(a, b, cap=2) <= 2:
        return True
    return False


async def _candidate_merges(
    pool: asyncpg.Pool, self_emails: set[str]
) -> list[asyncpg.Record]:
    """Find (telegram_person_id, email_person_id, email) triples where the
    LLM has tagged `email` as personal_email for telegram_person, AND the
    email has its own separate canonical.person row to merge in."""
    async with pool.acquire() as conn:
        # Build a SQL list of self-emails to exclude. raw.gmail_account emails
        # always count as self; the env var extends that with additional ones
        # (e.g. user's personal gmail).
        own_account = await conn.fetch("SELECT email FROM raw.gmail_account")
        exclude = set(self_emails) | {r["email"].lower() for r in own_account}

        rows = await conn.fetch(
            """
            WITH personal_emails AS (
              SELECT mp.person_id AS tg_person_id,
                     lower(e.value) AS email_addr
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
            JOIN canonical.person tg_person ON tg_person.id = pe.tg_person_id
            JOIN canonical.identity ei
              ON ei.source = 'email' AND ei.source_id = pe.email_addr
            JOIN canonical.person em_person ON em_person.id = ei.person_id
            WHERE tg_person.merged_into IS NULL
              AND em_person.merged_into IS NULL
              AND tg_person.id <> em_person.id
              AND NOT (pe.email_addr = ANY($1::text[]))
            ORDER BY pe.tg_person_id, pe.email_addr
            """,
            list(exclude),
        )
        # Name-compatibility filter happens in Python — too fiddly in SQL.
        return [r for r in rows if _names_compatible(r["tg_display_name"], r["em_display_name"])]


async def run(cfg: Config) -> int:
    pool = await asyncpg.create_pool(
        cfg.db_url, min_size=1, max_size=2, statement_cache_size=0
    )
    try:
        candidates = await _candidate_merges(pool, set(cfg.self_emails))
        log.info(
            "identity merger: %d candidate (tg→email) merges "
            "(self-email exclusions: %d explicit + all gmail accounts; "
            "name-compatibility filter applied)",
            len(candidates), len(cfg.self_emails),
        )

        merged = skipped = 0
        # Group by tg_person_id so we don't repeat the notes string for the
        # same target. (Doesn't matter for correctness, but keeps logs sane.)
        for r in candidates:
            tg_id = r["tg_person_id"]
            em_id = r["email_person_id"]
            tg_name = r["tg_display_name"]
            em_name = r["em_display_name"]
            email_addr = r["email_addr"]

            note = f"merged via LLM-verified personal_email {email_addr}"
            if cfg.dry_run:
                log.info("DRY MERGE: %s [%s]  ←  %s [%s]  (%s)",
                         tg_name, tg_id[:8], em_name, em_id[:8], email_addr)
                merged += 1
                continue
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        # Re-check the state inside the transaction — another
                        # run could have moved either side under us.
                        ok = await conn.fetchval(
                            """
                            SELECT 1
                              FROM canonical.person tg, canonical.person em
                             WHERE tg.id = $1::uuid
                               AND em.id = $2::uuid
                               AND tg.merged_into IS NULL
                               AND em.merged_into IS NULL
                            """,
                            tg_id, em_id,
                        )
                        if not ok:
                            skipped += 1
                            continue

                        # 1. Re-point every interaction at the winner so
                        #    "messages for person X" queries see the union.
                        await conn.execute(
                            """
                            UPDATE canonical.interaction
                               SET person_id = $1::uuid
                             WHERE person_id = $2::uuid
                            """,
                            tg_id, em_id,
                        )

                        # 1b. Re-point identities (dedup first to avoid the
                        #     unique constraint on (source, source_id)).
                        await conn.execute(
                            """
                            DELETE FROM canonical.identity i_loser
                             USING canonical.identity i_winner
                             WHERE i_loser.person_id = $2::uuid
                               AND i_winner.person_id = $1::uuid
                               AND i_winner.source    = i_loser.source
                               AND i_winner.source_id = i_loser.source_id
                            """,
                            tg_id, em_id,
                        )
                        await conn.execute(
                            """
                            UPDATE canonical.identity
                               SET person_id = $1::uuid
                             WHERE person_id = $2::uuid
                            """,
                            tg_id, em_id,
                        )

                        # 2. Re-point extracted signals. If the same (type,
                        #    value, source) row exists for both, drop the
                        #    loser's first (unique constraint would explode).
                        await conn.execute(
                            """
                            DELETE FROM memory.extracted_signal s_loser
                             WHERE s_loser.person_id = $2::uuid
                               AND EXISTS (
                                 SELECT 1 FROM memory.extracted_signal s_winner
                                  WHERE s_winner.person_id = $1::uuid
                                    AND s_winner.signal_type = s_loser.signal_type
                                    AND s_winner.value       = s_loser.value
                                    AND s_winner.source      = s_loser.source
                               )
                            """,
                            tg_id, em_id,
                        )
                        await conn.execute(
                            """
                            UPDATE memory.extracted_signal
                               SET person_id = $1::uuid
                             WHERE person_id = $2::uuid
                            """,
                            tg_id, em_id,
                        )

                        # 3. memory.profile is keyed on person_id; the loser
                        #    rarely has one (their email-only history hadn't
                        #    been profiled yet) but drop defensively to keep
                        #    the table consistent with merged_into.
                        await conn.execute(
                            "DELETE FROM memory.profile WHERE person_id = $1::uuid",
                            em_id,
                        )

                        # 4. Mark the merge + null out twenty_id so future
                        #    twenty_sync runs don't accidentally re-attach
                        #    Twenty data to the loser. (The actual Twenty
                        #    row stays — user can delete in the CRM UI.)
                        await conn.execute(
                            """
                            UPDATE canonical.person
                               SET merged_into = $1::uuid,
                                   twenty_id   = NULL,
                                   notes = COALESCE(notes || E'\n', '') || $2
                             WHERE id = $3::uuid
                            """,
                            tg_id, note, em_id,
                        )
                merged += 1
                if merged % 25 == 0:
                    log.info("progress: merged=%d skipped=%d", merged, skipped)
            except Exception:
                log.exception("merge failed: tg=%s em=%s", tg_id, em_id)
                skipped += 1

        log.info("identity merger done: merged=%d skipped=%d", merged, skipped)
        return 0
    finally:
        await pool.close()
