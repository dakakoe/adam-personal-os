"""Runner: index every chain, then report what moved.

Chains are indexed one after another rather than concurrently — the whole job
is a few thousand requests against rate-limited endpoints, and a stampede is
what gets an IP throttled. One chain failing is logged and skipped; it will be
picked up on the next run because its cursor never advanced.

Each chain has an ordered list of sources (Etherscan, NodeReal, Alchemy, public
RPC). If the preferred one errors, the next is tried before giving up, so a
provider outage degrades rather than stopping the run.
"""
from __future__ import annotations

import asyncio
import logging

from . import chains as C
from .config import Config
from .etherscan import Etherscan
from .indexer import sync_chain
from .rpc import Rpc

log = logging.getLogger("zerc20")


def _sources(client, chain: C.Chain, cfg: Config) -> list[tuple[object, str]]:
    """Usable log sources for this chain, best first."""
    out: list[tuple[object, str]] = []
    for kind in chain.sources:
        if kind == "etherscan" and chain.etherscan_chain_id and cfg.etherscan_key:
            out.append((Etherscan(client, cfg.etherscan_key, chain.etherscan_chain_id, chain.name),
                        "etherscan"))
        elif kind == "nodereal" and chain.nodereal_net and cfg.nodereal_key:
            out.append((Rpc(client, [f"https://{chain.nodereal_net}.nodereal.io/v1/{cfg.nodereal_key}"],
                            chain.name), "nodereal"))
        elif kind == "alchemy" and chain.alchemy_net and cfg.alchemy_key:
            out.append((Rpc(client, [f"https://{chain.alchemy_net}.g.alchemy.com/v2/{cfg.alchemy_key}"],
                            chain.name), "alchemy"))
        elif kind == "public":
            out.append((Rpc(client, list(chain.public_rpcs), chain.name, pace=chain.pace), "public"))
    return out


async def _run_async(cfg: Config, backfill: bool, only: list[str] | None,
                     last_blocks: int | None, from_block: int | None) -> int:
    import httpx

    pool = None
    if not cfg.dry_run:
        if not cfg.db_url:
            raise SystemExit("no database configured: set ZERC20_DATABASE_URL, or POSTGRES_USER/PASSWORD/DB")
        import asyncpg           # only needed when we actually write

        pool = await asyncpg.create_pool(cfg.db_url, min_size=1, max_size=4, statement_cache_size=0)

    results, failures = [], 0
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            for name, chain in C.CHAINS.items():
                if only and name not in only:
                    continue
                candidates = _sources(client, chain, cfg)
                if not candidates:
                    failures += 1
                    log.error("%s: no usable log source configured", name)
                    continue
                for source, label in candidates:
                    try:
                        stats = await sync_chain(pool, source, chain, cfg,
                                                 backfill=backfill, last_blocks=last_blocks,
                                                 from_block=from_block)
                        stats["source"] = label
                        results.append(stats)
                        log.info("%-10s via %-9s blocks %s-%s: %d raw, %d transfers, %d teleports, %d fees",
                                 name, label, stats.get("from"), stats.get("to"), stats.get("raw", 0),
                                 stats.get("transfers", 0), stats.get("teleports", 0), stats.get("fees", 0))
                        break
                    except Exception as e:  # noqa: BLE001 — try the next source, then move on
                        log.warning("%s: %s source failed: %s", name, label, str(e)[:160])
                else:
                    failures += 1
                    log.error("%s: every source failed", name)
    finally:
        if pool is not None:
            await pool.close()

    total_raw = sum(r.get("raw", 0) for r in results)
    log.info("done: %d chains indexed, %d raw logs stored, %d failed",
             len(results), total_raw, failures)
    # Partial success is still success: a failed chain retries on the next run.
    return 1 if failures and not results else 0


def run(cfg: Config, *, backfill: bool = False, only: list[str] | None = None,
        last_blocks: int | None = None, from_block: int | None = None) -> int:
    return asyncio.run(_run_async(cfg, backfill, only, last_blocks, from_block))
