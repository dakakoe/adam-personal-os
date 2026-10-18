"""Load bot/system wallets into zerc20.excluded_wallet.

Reads the bot feed's JSON shape on stdin — either the feed document itself
({"addresses": [...]}) or a plain list of {address, label, chain, kind} — and
inserts what is missing. Nothing is ever deleted: an address that leaves the
feed (retired, drained) must stay excluded or historical days would silently
change. Used by the daily zerc20-bot-watch task, which used to rewrite a Dune
query's SQL for the same purpose.

    cat addresses.json | python -m zerc20_indexer.exclusions
    python -m zerc20_indexer.exclusions --check      # report only, write nothing
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from .config import load as load_config

log = logging.getLogger("zerc20.exclusions")

CHAIN_BY_ID = {56: "BNB Chain", 137: "Polygon", 8217: "Kaia"}


def _rows(payload) -> list[dict]:
    """Normalise either input shape into insertable rows."""
    items = payload.get("addresses") if isinstance(payload, dict) else payload
    out: dict[str, dict] = {}
    for item in items or []:
        address = str(item.get("address") or "").lower().strip()
        if not address.startswith("0x") or len(address) != 42:
            continue
        out.setdefault(address, {
            "address": address,
            "kind": item.get("kind") or "bot",
            "label": item.get("label") or
                     f"{item.get('botKey', 'bot')} #{item.get('walletIndex', '?')} ({item.get('status', '?')})",
            "chain": item.get("chain") or CHAIN_BY_ID.get(item.get("chainId")),
            "source": item.get("source") or "feed",
        })
    return list(out.values())


async def _run(rows: list[dict], check_only: bool) -> int:
    import asyncpg

    cfg = load_config()
    if not cfg.db_url:
        raise SystemExit("no database configured: set ZERC20_DATABASE_URL, or POSTGRES_USER/PASSWORD/DB")
    conn = await asyncpg.connect(cfg.db_url)
    try:
        known = {r["address"] for r in await conn.fetch("SELECT address FROM zerc20.excluded_wallet")}
        missing = [r for r in rows if r["address"] not in known]
        print(f"feed rows {len(rows)} · already excluded {len(rows) - len(missing)} · missing {len(missing)}")
        for r in missing[:20]:
            print(f"  + {r['address']}  {r['label']}")
        if len(missing) > 20:
            print(f"  … and {len(missing) - 20} more")
        if missing and not check_only:
            await conn.executemany(
                "INSERT INTO zerc20.excluded_wallet (address, kind, label, chain, source) "
                "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (address) DO NOTHING",
                [(r["address"], r["kind"], r["label"], r["chain"], r["source"]) for r in missing])
            total = await conn.fetchval("SELECT count(*) FROM zerc20.excluded_wallet")
            print(f"inserted {len(missing)} · excluded_wallet now holds {total}")
        return len(missing)
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(prog="zerc20_indexer.exclusions")
    ap.add_argument("--check", action="store_true", help="report what is missing, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        raise SystemExit(f"stdin is not valid JSON: {e}")
    asyncio.run(_run(_rows(payload), args.check))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
