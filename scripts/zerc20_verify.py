#!/usr/bin/env python3
"""Compare the self-hosted zERC20 rows against the Dune rows they replace.

The cutover is a dual-run: both sources produce daily rows for the same
six chains, and the dashboard only switches once they agree. This prints the
disagreements rather than a verdict — a handful of recent-day gaps is the
indexer trailing the tip, while an old day that differs is a real bug.

Usage:
    python scripts/zerc20_verify.py --dune rows.json             # all wallets
    python scripts/zerc20_verify.py --dune clean.json --organic  # bots excluded

`rows.json` is the Dune result rows as JSON: either the raw
{"result": {"rows": [...]}} envelope or a bare list.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict

# A cent of drift is float noise, not a discrepancy worth reading.
USD_TOLERANCE = 0.01


def _load_dune(path: str) -> dict[tuple, tuple[int, float]]:
    with open(path) as fh:
        blob = json.load(fh)
    rows = blob["result"]["rows"] if isinstance(blob, dict) and "result" in blob else blob
    out: dict[tuple, tuple[int, float]] = {}
    for r in rows:
        key = (str(r["day"])[:10], r["chain"], r["token"], r["tx_type"])
        out[key] = (int(r["txs"] or 0), float(r["volume_usd"] or 0))
    return out


async def _load_db(view: str) -> dict[tuple, tuple[int, float]]:
    import asyncpg

    url = os.environ.get("ZERC20_DATABASE_URL")
    if not url:
        user, pw, db = (os.environ.get(k) for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"))
        if not (user and pw and db):
            raise SystemExit("set ZERC20_DATABASE_URL, or POSTGRES_USER/PASSWORD/DB")
        host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
        port = os.environ.get("POSTGRES_PORT", "5432")
        url = f"postgres://{user}:{pw}@{host}:{port}/{db}"
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            f"SELECT day, chain, token, tx_type, txs, volume_usd FROM zerc20.{view}")
    finally:
        await conn.close()
    return {(r["day"].isoformat(), r["chain"], r["token"], r["tx_type"]):
            (int(r["txs"]), float(r["volume_usd"] or 0)) for r in rows}


def _report(dune: dict, mine: dict) -> int:
    keys = sorted(set(dune) | set(mine))
    only_dune = [k for k in keys if k not in mine]
    only_mine = [k for k in keys if k not in dune]
    differing = [k for k in keys
                 if k in dune and k in mine
                 and (dune[k][0] != mine[k][0] or abs(dune[k][1] - mine[k][1]) > USD_TOLERANCE)]

    d_txs = sum(v[0] for v in dune.values()); d_usd = sum(v[1] for v in dune.values())
    m_txs = sum(v[0] for v in mine.values()); m_usd = sum(v[1] for v in mine.values())

    print(f"rows      dune={len(dune)}  indexer={len(mine)}  matching="
          f"{len(keys) - len(only_dune) - len(only_mine) - len(differing)}")
    print(f"txs       dune={d_txs:,}  indexer={m_txs:,}  diff={m_txs - d_txs:+,}")
    print(f"volume    dune=${d_usd:,.2f}  indexer=${m_usd:,.2f}  diff=${m_usd - d_usd:+,.2f}")

    for label, group in (("only in Dune", only_dune), ("only in indexer", only_mine)):
        if group:
            print(f"\n{label} ({len(group)}):")
            by_day = defaultdict(int)
            for day, *_ in group:
                by_day[day] += 1
            for day in sorted(by_day)[:10]:
                print(f"  {day}: {by_day[day]} rows")
            if len(by_day) > 10:
                print(f"  … and {len(by_day) - 10} more days")

    if differing:
        print(f"\ndiffering ({len(differing)}), worst 15 by tx gap:")
        for k in sorted(differing, key=lambda k: -abs(dune[k][0] - mine[k][0]))[:15]:
            dt, du = dune[k]; mt, mu = mine[k]
            print(f"  {k[0]} {k[1]:<10} {k[2]:<6} {k[3]:<7} "
                  f"txs {dt}->{mt} ({mt - dt:+})  usd {du:,.2f}->{mu:,.2f} ({mu - du:+,.2f})")

    ok = not (only_dune or differing)
    print("\n" + ("MATCH — safe to cut over" if ok else
                  "MISMATCH — do not cut over yet (rows missing from the indexer, or differing)"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dune", required=True, help="Dune result rows as JSON")
    ap.add_argument("--organic", action="store_true",
                    help="compare against v_daily_organic (bot/system wallets excluded)")
    args = ap.parse_args()
    view = "v_daily_organic" if args.organic else "v_daily"
    return _report(_load_dune(args.dune), asyncio.run(_load_db(view)))


if __name__ == "__main__":
    raise SystemExit(main())
