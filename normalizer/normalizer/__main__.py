from __future__ import annotations

import asyncio
import logging
import os
import sys

from . import config, sync


def _setup() -> None:
    os.umask(0o077)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


async def amain() -> int:
    cfg = config.load()
    await sync.run(cfg)
    return 0


def main() -> int:
    _setup()
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
