"""Scan raw.telegram_user.about and emit signals into memory.extracted_signal.

Bios are self-asserted, so every hit lands at confidence='high', source='telegram_bio'.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

from . import extract
from .config import Config
from .conversation_scanner import _upsert_signal

log = logging.getLogger(__name__)


async def _people_with_bios(pool: asyncpg.Pool, limit: int | None) -> list[dict[str, Any]]:
    """Join canonical.person to raw.telegram_user via canonical.identity for
    every person whose latest GetFullUser fetch landed a non-empty bio."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.id::text AS person_id, u.about
            FROM canonical.person p
            JOIN LATERAL (
              SELECT i.source_id
              FROM canonical.identity i
              WHERE i.person_id = p.id AND i.source = 'telegram'
              ORDER BY i.id LIMIT 1
            ) tg ON true
            JOIN raw.telegram_user u
              ON u.source_user_id = tg.source_id::bigint
            WHERE p.merged_into IS NULL
              AND u.about IS NOT NULL
              AND length(u.about) > 0
            ORDER BY (SELECT count(*) FROM canonical.interaction WHERE person_id = p.id) DESC
            """
            + (" LIMIT $1" if limit else ""),
            *([limit] if limit else []),
        )
    return [{"person_id": r["person_id"], "about": r["about"]} for r in rows]


async def run(cfg: Config) -> int:
    pool = await asyncpg.create_pool(
        cfg.db_url, min_size=1, max_size=2, statement_cache_size=0
    )
    try:
        people = await _people_with_bios(pool, cfg.limit)
        log.info("bio scan: %d people with non-empty bios", len(people))

        people_with_signals = 0
        signal_rows = 0
        for row in people:
            pid = row["person_id"]
            about = row["about"]
            hits = extract.extract_all(about, mode="liberal")
            if not hits:
                continue
            people_with_signals += 1
            # In a bio, the entire text is short — store it whole as evidence.
            ctx = about if len(about) <= 400 else about[:399] + "…"
            for sig_type, value, _raw_match in hits:
                evidence = {
                    "count": 1,
                    "sample_interaction_ids": [],
                    "sample_context": ctx,
                }
                if cfg.dry_run:
                    log.info("DRY BIO %s: %s [%s]", pid[:8], value, sig_type)
                    signal_rows += 1
                    continue
                async with pool.acquire() as conn:
                    await _upsert_signal(
                        conn,
                        person_id=pid,
                        signal_type=sig_type,
                        value=value,
                        confidence="high",
                        source="telegram_bio",
                        evidence=evidence,
                    )
                    signal_rows += 1

        log.info(
            "bio scan done: with_signals=%d signal_rows=%d",
            people_with_signals, signal_rows,
        )
        return 0
    finally:
        await pool.close()
