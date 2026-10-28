from __future__ import annotations

import logging
import os
import sys

from . import config, main as runner


def main() -> int:
    os.umask(0o077)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    return runner.run(config.load())


if __name__ == "__main__":
    raise SystemExit(main())
