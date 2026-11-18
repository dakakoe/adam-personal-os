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
from . import v2
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


async def _with_fallback(name: str, what: str, candidates, attempt):
    """Run `attempt(source)` against each source in turn until one succeeds."""
    for source, label in candidates:
        try:
            stats = await attempt(source)
            stats["source"] = label
            return stats
        except Exception as e:  # noqa: BLE001 — try the next source, then move on
            log.warning("%s %s: %s source failed: %s", name, what, label, str(e)[:160])
    log.error("%s %s: every source failed", name, what)
    return None


async def _run_async(cfg: Config, backfill: bool, only: list[str] | None,
                     last_blocks: int | None, from_block: int | None,
                     versions: tuple[str, ...] = ("v1", "v2")) -> int:
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

                # v1: the original protocol. Inactive chains (Kaia) keep their
                # history but are no longer scanned.
                if "v1" in versions and chain.active:
                    stats = await _with_fallback(name, "v1", candidates, lambda src: sync_chain(
                        pool, src, chain, cfg, backfill=backfill, last_blocks=last_blocks,
                        from_block=from_block))
                    if stats is None:
                        failures += 1
                    else:
                        stats["version"] = "v1"
                        results.append(stats)
                        log.info("%-10s v1 via %-9s blocks %s-%s: %d raw, %d transfers, %d teleports, %d fees",
                                 name, stats["source"], stats.get("from"), stats.get("to"),
                                 stats.get("raw", 0), stats.get("transfers", 0),
                                 stats.get("teleports", 0), stats.get("fees", 0))
                        if pool is not None and last_blocks is None:
                            await _snapshot_v1_tvl(pool, candidates, name, chain)

                # v2: the current protocol, on its own cursor from its own
                # deployment block.
                if "v2" in versions and name in v2.START_BLOCK:
                    stats = await _with_fallback(name, "v2", candidates, lambda src: v2.sync(
                        pool, src, name, cfg, chunk=C.chunk_for(chain, src.url),
                        backfill=backfill, last_blocks=last_blocks))
                    if stats is None:
                        failures += 1
                    else:
                        results.append(stats)
                        log.info("%-10s v2 via %-9s blocks %s-%s: %d raw, %d deposits, %d exits, "
                                 "%d withdrawals, %d sends, %d merges · tokens %s",
                                 name, stats["source"], stats.get("from"), stats.get("to"),
                                 stats.get("raw", 0), stats.get("deposits", 0), stats.get("exits", 0),
                                 stats.get("withdrawals", 0), stats.get("sends", 0),
                                 stats.get("merges", 0), ",".join(stats.get("tokens", [])))
    finally:
        if pool is not None:
            await pool.close()

    total_raw = sum(r.get("raw", 0) for r in results)
    log.info("done: %d chain passes, %d raw logs stored, %d failed",
             len(results), total_raw, failures)
    # Partial success is still success: a failed chain retries on the next run.
    return 1 if failures and not results else 0


async def _snapshot_v1_tvl(pool, candidates, name: str, chain: C.Chain) -> None:
    """Record what each v1 LiquidityManager holds. Best-effort: TVL is a side
    reading, so a failure here never fails the indexing pass."""
    managers = {lm: C.LIQUIDITY_MANAGERS[lm] for lm in chain.managers}
    for source, label in candidates:
        try:
            holdings = await v2.v1_holdings(source, managers)
            async with pool.acquire() as conn:
                await v2.snapshot_tvl(conn, source, name, "v1", holdings)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("%s v1 TVL: %s source failed: %s", name, label, str(e)[:120])


def run(cfg: Config, *, backfill: bool = False, only: list[str] | None = None,
        last_blocks: int | None = None, from_block: int | None = None,
        versions: tuple[str, ...] = ("v1", "v2")) -> int:
    return asyncio.run(_run_async(cfg, backfill, only, last_blocks, from_block, versions))


def redecode(cfg: Config, *, only: list[str] | None = None) -> int:
    """Replay stored raw logs through the v2 decoder for each v2 chain."""
    async def _go() -> int:
        import asyncpg

        if not cfg.db_url:
            raise SystemExit("no database configured: set ZERC20_DATABASE_URL, or POSTGRES_USER/PASSWORD/DB")
        conn = await asyncpg.connect(cfg.db_url)
        try:
            for name in v2.START_BLOCK:
                if only and name not in only:
                    continue
                stats = await v2.redecode(conn, name)
                log.info("%-10s v2 redecode: %s", name,
                         ", ".join(f"{k} {v}" for k, v in stats.items() if k != "chain"))
        finally:
            await conn.close()
        return 0
    return asyncio.run(_go())
