"""Fetch, store, decode. One chain at a time.

Raw logs are written verbatim and never updated (primary key
chain+tx_hash+log_index, conflicts ignored), so re-running a window is free and
a backfill can be repeated after a crash without producing duplicates. The
decoded tables are derived and can be rebuilt from raw_log alone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from . import chains as C
from .rpc import Rpc, RpcError

log = logging.getLogger("zerc20.indexer")


def _addr(topic: str) -> str:
    """32-byte topic word -> 20-byte address, lower-cased."""
    return "0x" + topic[-40:].lower()


def _hexint(value: str | None, default: int = 0) -> int:
    """Hex string to int, tolerating the empty forms providers really return.

    Etherscan hands back "0x" (and occasionally "") for a zero logIndex, which
    int(x, 16) rejects outright.
    """
    if not value:
        return default
    text = value[2:] if value.lower().startswith("0x") else value
    return int(text, 16) if text else default


def _word(data: str, index: int) -> int:
    """The `index`-th 32-byte word of an 0x-hex data payload, as an int."""
    body = data[2:] if data.startswith("0x") else data
    chunk = body[index * 64:(index + 1) * 64]
    return int(chunk, 16) if chunk else 0


def _scaled(raw: int, decimals: int) -> Decimal:
    return Decimal(raw) / (Decimal(10) ** decimals)


async def _resolve_start(conn, rpc: Rpc, chain: C.Chain, cfg) -> tuple[int, int]:
    """Cursor for this chain, creating it on first run."""
    if conn is not None:
        row = await conn.fetchrow(
            "SELECT start_block, last_block FROM zerc20.cursor WHERE chain = $1", chain.name)
        if row:
            return row["start_block"], row["last_block"]

    if chain.start_block is not None:
        start = chain.start_block          # published deployment block
    else:
        target = int(datetime.combine(cfg.start_date, datetime.min.time(),
                                      tzinfo=timezone.utc).timestamp())
        start = await rpc.find_block_at(target)
        log.info("%s: start block %d resolved for %s", chain.name, start, cfg.start_date)

    if conn is not None:
        await conn.execute(
            "INSERT INTO zerc20.cursor (chain, start_block, last_block) VALUES ($1, $2, $3) "
            "ON CONFLICT (chain) DO NOTHING", chain.name, start, start - 1)
    return start, start - 1


async def _store(conn, chain_name: str, logs: list[dict], rpc: Rpc) -> dict:
    """Persist one window: raw logs, block times, then the decoded rows."""
    counts = {"raw": 0, "transfers": 0, "teleports": 0, "fees": 0}
    if not logs:
        return counts

    raw_rows = []
    for lg in logs:
        topics = [t.lower() for t in lg.get("topics", [])]
        if not topics:
            continue                        # anonymous event: nothing to classify on
        raw_rows.append((chain_name, lg["transactionHash"].lower(), _hexint(lg.get("logIndex")),
                         _hexint(lg.get("blockNumber")), lg["address"].lower(), topics[0],
                         topics, lg.get("data") or "0x"))
    if not raw_rows:
        return counts

    await conn.executemany(
        "INSERT INTO zerc20.raw_log (chain, tx_hash, log_index, block_number, address, topic0, topics, data) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING", raw_rows)
    counts["raw"] = len(raw_rows)

    # Block timestamps, cached — eth_getLogs does not carry them everywhere.
    wanted = sorted({r[3] for r in raw_rows})
    known = {r["block_number"]: r["block_time"] for r in await conn.fetch(
        "SELECT block_number, block_time FROM zerc20.block_time "
        "WHERE chain = $1 AND block_number = ANY($2::bigint[])", chain_name, wanted)}
    missing = [n for n in wanted if n not in known]
    if missing:
        fetched = await rpc.block_times(missing)
        rows = [(chain_name, n, datetime.fromtimestamp(ts, tz=timezone.utc))
                for n, ts in fetched.items()]
        if rows:
            await conn.executemany(
                "INSERT INTO zerc20.block_time (chain, block_number, block_time) "
                "VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", rows)
        known.update({n: datetime.fromtimestamp(ts, tz=timezone.utc) for n, ts in fetched.items()})

    transfers, teleports, fees = [], [], []
    for chain_n, tx, idx, blk, address, topic0, topics, data in raw_rows:
        when = known.get(blk)
        if when is None:
            log.warning("%s: no timestamp for block %d, leaving log %s:%d undecoded",
                        chain_name, blk, tx, idx)
            continue
        meta = C.token_of(address)
        if meta is None:
            continue
        token, decimals = meta

        if topic0 == C.TRANSFER and len(topics) >= 3:
            frm, to = _addr(topics[1]), _addr(topics[2])
            # Only mints and burns are counted, matching the Dune scope.
            if frm == C.ZERO or to == C.ZERO:
                transfers.append((chain_n, tx, idx, token, when, when.date(), frm, to,
                                  _scaled(_word(data, 0), decimals)))
        elif topic0 == C.TELEPORT:
            teleports.append((chain_n, tx))
        elif topic0 == C.UNWRAPPED and address in C.LIQUIDITY_MANAGERS:
            fees.append((chain_n, tx, idx, token, when, when.date(),
                         _scaled(_word(data, 0), decimals),   # amountOut
                         _scaled(_word(data, 1), decimals)))  # feeAmount

    if transfers:
        await conn.executemany(
            "INSERT INTO zerc20.transfer (chain, tx_hash, log_index, token, block_time, day, "
            "from_addr, to_addr, amount) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) "
            "ON CONFLICT DO NOTHING", transfers)
    if teleports:
        await conn.executemany(
            "INSERT INTO zerc20.teleport_tx (chain, tx_hash) VALUES ($1,$2) "
            "ON CONFLICT DO NOTHING", sorted(set(teleports)))
    if fees:
        await conn.executemany(
            "INSERT INTO zerc20.unwrap_fee (chain, tx_hash, log_index, token, block_time, day, "
            "amount_out, fee_amount) VALUES ($1,$2,$3,$4,$5,$6,$7,$8) "
            "ON CONFLICT DO NOTHING", fees)

    counts.update(transfers=len(transfers), teleports=len(set(teleports)), fees=len(fees))
    return counts


async def sync_chain(pool, rpc: Rpc, chain: C.Chain, cfg, *, backfill: bool = False,
                     last_blocks: int | None = None, from_block: int | None = None) -> dict:
    """Index one chain from its cursor (or its start block) to the safe tip.

    `last_blocks` scans only that many blocks back from the tip and ignores the
    cursor — a smoke test that needs no backfill and no database."""
    tip = await rpc.block_number()
    safe_tip = tip - cfg.confirmations
    conn = None if cfg.dry_run else await pool.acquire()
    try:
        if last_blocks is not None:
            from_block = max(1, safe_tip - last_blocks)
        elif from_block is None:
            start, last = await _resolve_start(conn, rpc, chain, cfg)
            from_block = start if backfill else max(last + 1, start)
        else:
            # Explicit override: make sure a cursor row exists so progress sticks.
            await _resolve_start(conn, rpc, chain, cfg)
        if from_block > safe_tip:
            return {"chain": chain.name, "from": from_block, "to": safe_tip, "raw": 0,
                    "transfers": 0, "teleports": 0, "fees": 0, "note": "up to date"}

        totals = {"raw": 0, "transfers": 0, "teleports": 0, "fees": 0}
        windows = 0
        chunk = C.chunk_for(chain, rpc.url)
        async for logs, window_end in rpc.get_logs(C.addresses(chain), from_block, safe_tip, chunk):
            windows += 1
            if cfg.dry_run:
                totals["raw"] += len(logs)
                for lg in logs:
                    t0 = (lg.get("topics") or [""])[0].lower()
                    if t0 == C.TELEPORT:
                        totals["teleports"] += 1
                    elif t0 == C.UNWRAPPED:
                        totals["fees"] += 1
                    elif t0 == C.TRANSFER:
                        totals["transfers"] += 1
            else:
                for key, value in (await _store(conn, chain.name, logs, rpc)).items():
                    totals[key] += value
                await conn.execute(
                    "UPDATE zerc20.cursor SET last_block = $2, updated_at = now() WHERE chain = $1",
                    chain.name, window_end)
            if windows % 25 == 0:
                log.info("%s: at block %d/%d (%d raw logs so far)",
                         chain.name, window_end, safe_tip, totals["raw"])
        return {"chain": chain.name, "from": from_block, "to": safe_tip, "windows": windows, **totals}
    finally:
        if conn is not None:
            await pool.release(conn)
