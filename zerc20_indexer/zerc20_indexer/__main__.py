from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import replace

from . import chains as C
from . import config, main as runner


def main() -> int:
    os.umask(0o077)
    ap = argparse.ArgumentParser(prog="zerc20_indexer", description="Index zERC20 logs into Postgres.")
    ap.add_argument("--backfill", action="store_true",
                    help="re-scan from each chain's start block (idempotent; safe to repeat)")
    ap.add_argument("--chain", action="append", choices=list(C.CHAINS),
                    help="limit to one chain; repeatable")
    ap.add_argument("--last", type=int, metavar="N",
                    help="scan only the last N blocks, ignoring the cursor (smoke test)")
    ap.add_argument("--from-block", type=int, metavar="N",
                    help="start this run at block N instead of the cursor; useful to skip a "
                         "range known to be empty (the cursor still only moves forward)")
    ap.add_argument("--dry-run", action="store_true", help="fetch and count, write nothing")
    ap.add_argument("--version", choices=["v1", "v2", "all"], default="all",
                    help="which protocol to index (default: both)")
    ap.add_argument("--redecode", action="store_true",
                    help="rebuild the v2 tables from stored raw logs, with no RPC calls")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr)
    # httpx logs every request at INFO; a backfill is thousands of them.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = config.load()
    if args.dry_run:
        cfg = replace(cfg, dry_run=True)
    if args.redecode:
        return runner.redecode(cfg, only=args.chain)
    versions = ("v1", "v2") if args.version == "all" else (args.version,)
    return runner.run(cfg, backfill=args.backfill, only=args.chain, last_blocks=args.last,
                      from_block=args.from_block, versions=versions)


if __name__ == "__main__":
    raise SystemExit(main())
