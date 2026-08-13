from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import pgvector.asyncpg

from .config import Config


async def _register_vector(conn: asyncpg.Connection) -> None:
    """asyncpg needs to learn the pgvector type once per connection so
    we can pass / receive vectors as native python lists."""
    await pgvector.asyncpg.register_vector(conn)

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()
_cfg: Config | None = None


def configure(cfg: Config) -> None:
    """Stash the config once at startup so tools don't have to re-load env."""
    global _cfg
    _cfg = cfg


async def pool() -> asyncpg.Pool:
    """Lazy pool initializer. Idempotent and concurrency-safe."""
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            assert _cfg is not None, "db.configure(cfg) must be called before pool()"
            _pool = await asyncpg.create_pool(
                _cfg.db_url,
                min_size=1,
                max_size=4,
                statement_cache_size=0,
                init=_register_vector,
            )
    return _pool


def _truncate(text: str | None, max_len: int = 500) -> str | None:
    if text is None:
        return None
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert asyncpg Record to JSON-friendly dict; coerce UUIDs and datetimes."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "hex") and not isinstance(v, (bytes, bytearray)):
            # UUID — use str() which gives the canonical hyphenated form
            out[k] = str(v)
        else:
            out[k] = v
    return out
