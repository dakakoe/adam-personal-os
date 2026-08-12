"""Daily worker: materialize due auto-post planned transactions.

For each active, auto_post planned template whose next_date <= today, create the
fin_transaction(s) up to today (catching up any missed occurrences) and advance
next_date. Idempotent via source_kind='planned', source_ref='<id>:<date>' so a
re-run never duplicates. Reminder-only templates (auto_post=false) are left for
the user to post from the UI."""
from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg

from .config import Config

log = logging.getLogger("fin_planned")


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _advance(d: date, freq: str, byweekday) -> date:
    if freq == "daily":
        return d + timedelta(days=1)
    if freq == "weekly":
        days = sorted({int(x) for x in (byweekday or [])}) or [d.weekday()]
        for i in range(1, 8):
            nd = d + timedelta(days=i)
            if nd.weekday() in days:
                return nd
        return d + timedelta(days=7)
    if freq == "monthly":
        return _add_months(d, 1)
    if freq == "yearly":
        return _add_months(d, 12)
    return d + timedelta(days=1)


_INSERT = """
INSERT INTO memory.fin_transaction
  (txn_date, outflow_account_id, outflow_asset_id, outflow_amount,
   inflow_account_id, inflow_asset_id, inflow_amount,
   category_key, payee_text, note, source_kind, source_ref)
VALUES ($1::date, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'planned', $11)
ON CONFLICT (source_kind, source_ref) WHERE source_ref IS NOT NULL DO NOTHING
"""


async def _run_async(cfg: Config) -> int:
    today = datetime.now(ZoneInfo(cfg.timezone)).date()
    pool = await asyncpg.create_pool(cfg.db_url, min_size=1, max_size=2, statement_cache_size=0)
    try:
        async with pool.acquire() as conn:
            due = await conn.fetch(
                """
                SELECT * FROM memory.fin_planned_transaction
                 WHERE deleted_at IS NULL AND active = true AND auto_post = true
                   AND next_date <= $1::date
                """, today)
            posted = 0
            for p in due:
                nd = p["next_date"]
                guard = 0
                async with conn.transaction():
                    while nd <= today and guard < 400:
                        guard += 1
                        ref = f"{p['id']}:{nd.isoformat()}"
                        if cfg.dry_run:
                            log.info("DRY post %s on %s", p.get("name") or p["id"], nd)
                        else:
                            await conn.execute(
                                _INSERT, nd,
                                p["outflow_account_id"], p["outflow_asset_id"], p["outflow_amount"],
                                p["inflow_account_id"], p["inflow_asset_id"], p["inflow_amount"],
                                p["category_key"], p["payee_text"], p["note"], ref)
                            posted += 1
                        nd = _advance(nd, p["freq"], p["byweekday"])
                    if not cfg.dry_run:
                        await conn.execute(
                            "UPDATE memory.fin_planned_transaction SET next_date = $2 WHERE id = $1",
                            p["id"], nd)
        log.info("fin_planned: %d templates due, posted %d occurrences (through %s)", len(due), posted, today)
        return 0
    finally:
        await pool.close()


def run(cfg: Config) -> int:
    return asyncio.run(_run_async(cfg))
