from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from . import config, contacts, db, gcal, sync


def _setup() -> None:
    os.umask(0o077)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


async def cmd_list_accounts() -> int:
    cfg = config.load()
    pool = await db.connect(cfg.db_url)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT email, status, last_sync_at, "
                "(refresh_token IS NOT NULL) AS has_token FROM raw.gmail_account ORDER BY email"
            )
        for r in rows:
            print(f"  {r['email']:40s}  status={r['status']:12s}  has_token={r['has_token']}  last_sync={r['last_sync_at']}")
        if not rows:
            print("  (no accounts yet — run gmail-oauth on your Mac to add one)")
    finally:
        await pool.close()
    return 0


def main() -> int:
    _setup()
    p = argparse.ArgumentParser(prog="gmail")
    p.add_argument("command", nargs="?", default="sync",
                   choices=["sync", "backfill", "contacts-sync", "calendar-sync", "list-accounts"],
                   help="sync: forward incremental email (newer than what we have); "
                        "backfill: walk backwards email (older than what we have); "
                        "contacts-sync: pull Google Contacts via People API; "
                        "calendar-sync: pull upcoming Calendar events (Phase 4 v2); "
                        "list-accounts: print configured accounts")
    args = p.parse_args()
    cfg = config.load()
    if args.command == "list-accounts":
        return asyncio.run(cmd_list_accounts())
    if args.command == "contacts-sync":
        return asyncio.run(contacts.run(cfg))
    if args.command == "calendar-sync":
        return asyncio.run(gcal.run(cfg))
    mode = "backfill" if args.command == "backfill" else "forward"
    return asyncio.run(sync.run(cfg, mode=mode))


if __name__ == "__main__":
    raise SystemExit(main())
