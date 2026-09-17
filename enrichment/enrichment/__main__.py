from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from . import bio_scanner, candidate_generator, config, conversation_scanner, identity_merger


def _setup() -> None:
    os.umask(0o077)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main() -> int:
    _setup()
    parser = argparse.ArgumentParser(prog="enrichment")
    parser.add_argument(
        "command",
        choices=["scan-conversations", "scan-bios", "merge-identities", "generate-candidates"],
        help="scan-conversations: regex pass over canonical.interaction bodies; "
             "scan-bios: regex pass over raw.telegram_user.about; "
             "merge-identities: AUTO collapse canonical.person rows that share an LLM-verified personal_email; "
             "generate-candidates: write pending merge_candidate rows for human review (LLM+name+phone signals)",
    )
    args = parser.parse_args()
    cfg = config.load()
    if args.command == "scan-conversations":
        return asyncio.run(conversation_scanner.run(cfg))
    if args.command == "merge-identities":
        return asyncio.run(identity_merger.run(cfg))
    if args.command == "generate-candidates":
        return asyncio.run(candidate_generator.run(cfg))
    return asyncio.run(bio_scanner.run(cfg))


if __name__ == "__main__":
    raise SystemExit(main())
