from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from . import config, run, vcard


def _setup() -> None:
    os.umask(0o077)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _build_db_url() -> str:
    user = os.environ["POSTGRES_USER"]
    pw = os.environ["POSTGRES_PASSWORD"]
    db = os.environ["POSTGRES_DB"]
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgres://{user}:{pw}@{host}:{port}/{db}"


def main() -> int:
    _setup()
    p = argparse.ArgumentParser(prog="gmail_import")
    sub = p.add_subparsers(dest="command")

    sub.add_parser(
        "mbox",
        help="Import a Gmail Takeout .mbox (configured via env: GMAIL_IMPORT_MBOX, GMAIL_IMPORT_ACCOUNT, ...)",
    )

    vp = sub.add_parser("vcard", help="Import one or more Google Contacts .vcf files")
    vp.add_argument("paths", nargs="+", help="Absolute paths to .vcf files")

    # Default behaviour preserved: invoking with no subcommand runs mbox.
    args = p.parse_args()
    command = args.command or "mbox"

    if command == "vcard":
        db_url = os.environ.get("GMAIL_IMPORT_DATABASE_URL") or _build_db_url()
        return asyncio.run(vcard.run(db_url, args.paths))

    cfg = config.load()
    return asyncio.run(run.run(cfg))


if __name__ == "__main__":
    raise SystemExit(main())
