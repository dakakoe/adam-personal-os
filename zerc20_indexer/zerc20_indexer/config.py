from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Config:
    db_url: str
    # Provider keys are optional: without them the indexer falls back to public
    # endpoints, which work but cap eth_getLogs at much smaller block ranges.
    alchemy_key: str | None
    nodereal_key: str | None
    etherscan_key: str | None
    # Nothing before this date is indexed. zERC20's first mainnet activity was
    # 2026-02-04; January is included as a cheap safety margin.
    start_date: date
    # How far behind the tip to stop, so a reorg cannot strand a row that the
    # append-only raw_log would never revisit.
    confirmations: int
    dry_run: bool


def load() -> Config:
    return Config(
        # Empty when no database is configured; only the writing paths care,
        # so --dry-run works on a laptop with nothing but network access.
        db_url=os.environ.get("ZERC20_DATABASE_URL") or _build_db_url(),
        alchemy_key=os.environ.get("ALCHEMY_API_KEY") or None,
        nodereal_key=os.environ.get("NODEREAL_API_KEY") or None,
        etherscan_key=os.environ.get("ETHERSCAN_API_KEY") or None,
        start_date=date.fromisoformat(os.environ.get("ZERC20_START_DATE", "2026-01-01")),
        confirmations=int(os.environ.get("ZERC20_CONFIRMATIONS", "12")),
        dry_run=os.environ.get("ZERC20_DRY_RUN", "").lower() in ("1", "true", "yes"),
    )


def _build_db_url() -> str:
    user = os.environ.get("POSTGRES_USER")
    pw = os.environ.get("POSTGRES_PASSWORD")
    db = os.environ.get("POSTGRES_DB")
    if not (user and pw and db):
        return ""
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgres://{user}:{pw}@{host}:{port}/{db}"
