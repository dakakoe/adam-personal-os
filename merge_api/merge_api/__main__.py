from __future__ import annotations

import logging
import os
import sys

import uvicorn

from . import config as cfg_mod


def main() -> int:
    os.umask(0o077)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    cfg = cfg_mod.load()
    uvicorn.run(
        "merge_api.app:app",
        host=cfg.host, port=cfg.port,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
