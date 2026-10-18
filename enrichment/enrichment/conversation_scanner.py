"""Per-person scan of canonical.interaction bodies. Emits rows into
memory.extracted_signal.

Confidence rules:
  - Inbound message (counterparty wrote it):  high
  - Outbound message (you wrote it):           medium
    (you might have typed someone else's email about them; can't tell)

A signal is recorded once per (person, type, value, source). Re-running the
scanner upserts evidence (count, last_seen_at, sample interaction_ids) without
inflating row count.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

import asyncpg

from . import extract
from .config import Config

log = logging.getLogger(__name__)


# Direction → (confidence, source) for messages we extract from.
# Outbound is excluded by design: emails/URLs *you* typed are nearly always
# about third parties (replying to a forwarded thread, sharing a link, asking
# "is your email X?"). Their precision is too low to store as a signal.
# Inbound stays at "medium" because the counterparty can still be quoting,
# forwarding, or referring to someone else; only bios are "high".
_DIRECTION_RULES = {
    "inbound":  ("medium", "conversation_inbound"),
}


async def _people_with_messages(pool: asyncpg.Pool, limit: int | None) -> list[str]:
    """Return person_ids that have at least one body-bearing interaction.
    Sorted by message count desc so the busiest people get processed first —
    useful when --limit is set for a smoke test."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT person_id::text AS person_id
            FROM canonical.interaction
            WHERE body IS NOT NULL AND length(body) > 0
            GROUP BY person_id
            ORDER BY count(*) DESC
            """
            + (" LIMIT $1" if limit else ""),
            *([limit] if limit else []),
        )
    return [r["person_id"] for r in rows]


async def _scan_one_person(
    pool: asyncpg.Pool,
    person_id: str,
    sample_chars: int,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Walk every body of one person; return aggregated signals keyed by
    (signal_type, value, source). Each aggregate carries count, the highest
    confidence seen for that source, sample interaction_ids, and a sample
    context window."""
    agg: dict[tuple[str, str, str], dict[str, Any]] = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text AS interaction_id, direction, body
            FROM canonical.interaction
            WHERE person_id = $1::uuid
              AND body IS NOT NULL
              AND length(body) > 0
            ORDER BY occurred_at ASC
            """,
            person_id,
        )

    for row in rows:
        direction = row["direction"]
        rule = _DIRECTION_RULES.get(direction)
        if rule is None:
            continue
        confidence, source = rule
        for sig_type, value, raw_match in extract.extract_all(row["body"]):
            key = (sig_type, value, source)
            slot = agg.get(key)
            if slot is None:
                slot = {
                    "confidence": confidence,
                    "count": 0,
                    "sample_interaction_ids": [],
                    "sample_context": extract.sample_context(
                        row["body"], raw_match, around=sample_chars
                    ),
                }
                agg[key] = slot
            slot["count"] += 1
            if len(slot["sample_interaction_ids"]) < 3:
                slot["sample_interaction_ids"].append(row["interaction_id"])

    return agg


async def _upsert_signal(
    conn: asyncpg.Connection,
    *,
    person_id: str,
    signal_type: str,
    value: str,
    confidence: str,
    source: str,
    evidence: dict[str, Any],
) -> None:
    """Insert or refresh one extracted_signal row. On conflict we bump count
    + last_seen_at and overwrite the sample context (cheap; latest run wins)
    but never downgrade confidence."""
    await conn.execute(
        """
        INSERT INTO memory.extracted_signal
          (person_id, signal_type, value, confidence, source, evidence)
        VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb)
        ON CONFLICT (person_id, signal_type, value, source) DO UPDATE SET
          last_seen_at = now(),
          confidence   = CASE
                           WHEN EXCLUDED.confidence = 'high' THEN 'high'
                           WHEN memory.extracted_signal.confidence = 'high' THEN 'high'
                           WHEN EXCLUDED.confidence = 'medium' OR memory.extracted_signal.confidence = 'medium' THEN 'medium'
                           ELSE 'low'
                         END,
          evidence     = EXCLUDED.evidence
        """,
        person_id, signal_type, value, confidence, source,
        json.dumps(evidence, default=str),
    )


async def run(cfg: Config) -> int:
    pool = await asyncpg.create_pool(
        cfg.db_url, min_size=1, max_size=4, statement_cache_size=0
    )
    try:
        people = await _people_with_messages(pool, cfg.limit)
        log.info("conversation scan: %d people to process", len(people))

        people_scanned = 0
        people_with_signals = 0
        signal_rows = 0
        for pid in people:
            agg = await _scan_one_person(pool, pid, cfg.sample_chars)
            people_scanned += 1
            if not agg:
                continue
            people_with_signals += 1
            for (sig_type, value, source), slot in agg.items():
                evidence = {
                    "count": slot["count"],
                    "sample_interaction_ids": slot["sample_interaction_ids"],
                    "sample_context": slot["sample_context"],
                }
                if cfg.dry_run:
                    log.info(
                        "DRY %s: %s [%s/%s] x%d  ctx=%r",
                        pid[:8], value, sig_type, source, slot["count"],
                        slot["sample_context"][:80],
                    )
                    signal_rows += 1
                    continue
                async with pool.acquire() as conn:
                    await _upsert_signal(
                        conn,
                        person_id=pid,
                        signal_type=sig_type,
                        value=value,
                        confidence=slot["confidence"],
                        source=source,
                        evidence=evidence,
                    )
                    signal_rows += 1

            if people_scanned % 100 == 0:
                log.info(
                    "progress: scanned=%d with_signals=%d signal_rows=%d",
                    people_scanned, people_with_signals, signal_rows,
                )

        log.info(
            "conversation scan done: scanned=%d with_signals=%d signal_rows=%d",
            people_scanned, people_with_signals, signal_rows,
        )
        return 0
    finally:
        await pool.close()
