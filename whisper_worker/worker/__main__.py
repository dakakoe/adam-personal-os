from __future__ import annotations

import asyncio
import logging
import os
import sys

from . import config, main as runner


def _setup() -> None:
    os.umask(0o077)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main() -> int:
    _setup()
    cfg = config.load()
    asyncio.run(runner.run(cfg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
