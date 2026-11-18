"""zERC20 v2: token registry, log decoding, and TVL snapshots.

v2 (October 2026) is a different protocol from v1, not a new version of it:
deposits wrap into per-chain zTokens, balances become notes in a Merkle tree,
and a relayer submits sends and withdrawals through one Verifier. Every v2
contract has the same address on all five chains; only the deployment block
differs. Source: docs.zerc20.io/developers/contracts, verified on-chain
2026-11-03 against every chain.

What v2 makes public, and what it does not: deposits (zToken Wrapped), exits
(zToken Unwrapped) and withdrawals (Verifier Transacted, with the exact fee)
carry amounts. A private SEND is a Transacted with withdrawAmount = 0 and only
note hashes — the amount is hidden by design. Sends are counted, never valued.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from .indexer import _addr, _hexint, _scaled, _word, store_raw

log = logging.getLogger("zerc20.v2")

FACTORY = "0xfb488dc9ecdeed12e126986b8bb441d6b14cde58"
VERIFIER = "0x4d14355c3fc905446908d70190eadf36f34db711"
NATIVE = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"

# Deployment blocks from the contracts page; nothing v2 exists before them.
START_BLOCK = {
    "Ethereum": 26_251_934,
    "Base": 52_964_834,
    "Arbitrum": 517_395_706,
    "BNB Chain": 129_236_066,
    "Polygon": 96_009_863,
}

# keccak256 of each signature; Transacted was also matched against live logs.
# The zToken Wrapped/Unwrapped take 3 arguments, so their topics differ from
# v1's 4-argument LiquidityManager events of the same name.
WRAPPED = "0x29b3b451f9779df34ec8a67ec6c96fa910b17a579a66b7428d94f481be6900d9"
UNWRAPPED = "0x742cbb4a6bddd5e23aa0c14356065c236bdbc921cddb7f1f763161eb2030f3ef"
TRANSACTED = "0x059bde18995097caf5697f068da667fa3ee11141d340e4f7baf055647026d2eb"
MERGED = "0xac59b10792715f1944ad7cbc8985e571f18cb61810f56147514aabb67f6567cb"
FEE_ACCRUED = "0x32cd704c52ba1f1d9f0fedbd5990cef471b2c747a676f2573d761382af8066f1"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO = "0x0000000000000000000000000000000000000000"
RAGE_EXITED = "0x5c82c0e35e43057d3e38e1c412d3c4997c8607b91a403d2049d63f8d36d952b5"

# Function selectors (first 4 bytes of keccak256).
SEL_TOKEN_COUNT = "0x9f181b5e"        # tokenCount()
SEL_TOKEN_BY_ID = "0xf02d012a"        # tokenById(uint32)
SEL_UNDERLYING_ASSET = "0x7158da7c"   # underlyingAsset()       — v2 zToken
SEL_UNDERLYING_TOKEN = "0x2495a599"   # underlyingToken()       — v1 LiquidityManager
SEL_SYMBOL = "0x95d89b41"             # symbol()
SEL_DECIMALS = "0x313ce567"           # decimals()
SEL_BALANCE_OF = "0x70a08231"         # balanceOf(address)


def _word_arg(value: int | str) -> str:
    """ABI-encode one static argument as a 32-byte word."""
    if isinstance(value, int):
        return hex(value)[2:].rjust(64, "0")
    return value.lower().removeprefix("0x").rjust(64, "0")


def _decode_string(hexdata: str | None) -> str | None:
    if not hexdata or hexdata == "0x":
        return None
    body = hexdata[2:]
    try:
        length = int(body[64:128], 16)
        return bytes.fromhex(body[128:128 + 2 * length]).decode()
    except (ValueError, UnicodeDecodeError):
        return None


async def refresh_registry(conn, source, chain_name: str) -> list[dict]:
    """Read ZERC20Factory and upsert every token for this chain.

    Token ids are assigned per chain (zJPYC is 3 on Polygon), so the registry is
    always keyed by (chain, token_id). A token listed tomorrow is picked up on
    the next run with no code change.
    """
    count = _hexint(await source.eth_call(FACTORY, SEL_TOKEN_COUNT))
    tokens = []
    for token_id in range(1, count + 1):
        ztoken = _addr(await source.eth_call(FACTORY, SEL_TOKEN_BY_ID + _word_arg(token_id)))
        underlying = _addr(await source.eth_call(ztoken, SEL_UNDERLYING_ASSET))
        symbol = _decode_string(await source.eth_call(ztoken, SEL_SYMBOL)) or f"z?{token_id}"
        decimals = _hexint(await source.eth_call(ztoken, SEL_DECIMALS)) or 18
        tokens.append({"token_id": token_id, "ztoken": ztoken, "underlying": underlying,
                       "symbol": symbol, "decimals": decimals})
    if conn is not None and tokens:
        await conn.executemany(
            "INSERT INTO zerc20.v2_token (chain, token_id, ztoken, underlying, symbol, decimals) "
            "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (chain, token_id) DO UPDATE SET "
            "ztoken = EXCLUDED.ztoken, underlying = EXCLUDED.underlying, "
            "symbol = EXCLUDED.symbol, decimals = EXCLUDED.decimals, seen_at = now()",
            [(chain_name, t["token_id"], t["ztoken"], t["underlying"], t["symbol"], t["decimals"])
             for t in tokens])
    return tokens


def addresses(tokens: list[dict]) -> list[str]:
    """Everything whose logs v2 needs on a chain: the Verifier and each zToken."""
    return [VERIFIER] + [t["ztoken"] for t in tokens]


async def store(conn, chain_name: str, logs: list[dict], source, tokens: list[dict]) -> dict:
    """Persist a window of v2 logs: raw first, then the decoded tables."""
    raw_rows, known = await store_raw(conn, chain_name, logs, source)
    return await decode(conn, chain_name, raw_rows, known, tokens)


async def decode(conn, chain_name: str, raw_rows: list, known: dict, tokens: list[dict]) -> dict:
    """Turn stored raw logs into the v2 tables. Idempotent: every insert
    ignores conflicts, so replaying the same logs changes nothing."""
    counts = {"raw": len(raw_rows), "deposits": 0, "exits": 0, "transfers": 0, "sends": 0,
              "withdrawals": 0, "merges": 0, "fees": 0, "rage_exits": 0}
    if not raw_rows:
        return counts

    by_ztoken = {t["ztoken"]: t for t in tokens}
    by_id = {t["token_id"]: t for t in tokens}
    wraps, unwraps, transfers, transacts, merges, fees, exits = [], [], [], [], [], [], []

    for chain_n, tx, idx, blk, address, topic0, topics, data in raw_rows:
        when = known.get(blk)
        if when is None:
            log.warning("%s: no timestamp for block %d, leaving %s:%d undecoded", chain_name, blk, tx, idx)
            continue

        if topic0 in (WRAPPED, UNWRAPPED) and address in by_ztoken and len(topics) >= 3:
            token = by_ztoken[address]
            row = (chain_n, tx, idx, address, when, when.date(), _addr(topics[1]), _addr(topics[2]),
                   _scaled(_word(data, 0), token["decimals"]))
            (wraps if topic0 == WRAPPED else unwraps).append(row)

        elif topic0 == TRANSFER and address in by_ztoken and len(topics) >= 3:
            frm, dst = _addr(topics[1]), _addr(topics[2])
            # Mints and burns are already the deposits, payouts and exits above.
            if frm != ZERO and dst != ZERO:
                transfers.append((chain_n, tx, idx, address, when, when.date(), frm, dst,
                                  _scaled(_word(data, 0), by_ztoken[address]["decimals"])))

        elif address == VERIFIER and topic0 == TRANSACTED and len(topics) >= 3:
            token = by_id.get(_hexint(topics[2]))
            if token is None:
                continue                         # unknown id: registry refresh will catch it
            amount = _word(data, 1)
            kind = "withdraw" if amount else "send"
            transacts.append((chain_n, tx, idx, token["token_id"], when, when.date(),
                              _addr(topics[1]), kind,
                              "0x" + data[2:][24:64] if kind == "withdraw" else None,
                              _scaled(amount, token["decimals"]),
                              _scaled(_word(data, 2), token["decimals"])))

        elif address == VERIFIER and topic0 == MERGED and len(topics) >= 3:
            token = by_id.get(_hexint(topics[2]))
            if token is not None:
                merges.append((chain_n, tx, idx, token["token_id"], when, when.date(), _word(data, 0)))

        elif address == VERIFIER and topic0 == FEE_ACCRUED and len(topics) >= 3:
            token = by_id.get(_hexint(topics[1]))
            if token is not None:
                fees.append((chain_n, tx, idx, token["token_id"], when, when.date(),
                             _addr(topics[2]), _scaled(_word(data, 0), token["decimals"])))

        elif address == VERIFIER and topic0 == RAGE_EXITED and len(topics) >= 3:
            token = by_id.get(_hexint(topics[2]))
            if token is not None:
                exits.append((chain_n, tx, idx, token["token_id"], when, when.date(),
                              "0x" + data[2:][24:64], _scaled(_word(data, 1), token["decimals"])))

    async def put(table: str, cols: str, rows: list):
        if rows:
            marks = ",".join(f"${i}" for i in range(1, len(rows[0]) + 1))
            await conn.executemany(
                f"INSERT INTO zerc20.{table} ({cols}) VALUES ({marks}) ON CONFLICT DO NOTHING", rows)

    await put("v2_wrap", "chain, tx_hash, log_index, ztoken, block_time, day, caller, receiver, amount", wraps)
    await put("v2_unwrap", "chain, tx_hash, log_index, ztoken, block_time, day, caller, receiver, amount", unwraps)
    await put("v2_transfer", "chain, tx_hash, log_index, ztoken, block_time, day, from_addr, to_addr, amount",
              transfers)
    await put("v2_transact", "chain, tx_hash, log_index, token_id, block_time, day, caller, kind, "
                             "withdraw_recipient, withdraw_amount, fee_amount", transacts)
    await put("v2_merge", "chain, tx_hash, log_index, token_id, block_time, day, input_count", merges)
    await put("v2_fee_accrued", "chain, tx_hash, log_index, token_id, block_time, day, recipient, amount", fees)
    await put("v2_rage_exit", "chain, tx_hash, log_index, token_id, block_time, day, recipient, amount", exits)

    counts.update(deposits=len(wraps), exits=len(unwraps), transfers=len(transfers),
                  sends=sum(1 for t in transacts if t[7] == "send"),
                  withdrawals=sum(1 for t in transacts if t[7] == "withdraw"),
                  merges=len(merges), fees=len(fees), rage_exits=len(exits))
    return counts


async def redecode(conn, chain_name: str) -> dict:
    """Rebuild a chain's v2 tables from raw_log alone — no RPC calls.

    raw_log is the source of truth: a decoder fix, or a new decoded table,
    is applied by replaying what is already stored rather than re-fetching it.
    """
    rows = await conn.fetch(
        "SELECT token_id, ztoken, underlying, symbol, decimals FROM zerc20.v2_token WHERE chain = $1",
        chain_name)
    tokens = [dict(r) for r in rows]
    if not tokens:
        return {"chain": chain_name, "note": "no v2 registry yet; run the indexer first"}
    raw = await conn.fetch(
        "SELECT chain, tx_hash, log_index, block_number, address, topic0, topics, data "
        "FROM zerc20.raw_log WHERE chain = $1 AND address = ANY($2::text[]) "
        "ORDER BY block_number, log_index", chain_name, addresses(tokens))
    raw_rows = [tuple(r) for r in raw]
    known = {r["block_number"]: r["block_time"] for r in await conn.fetch(
        "SELECT block_number, block_time FROM zerc20.block_time WHERE chain = $1 "
        "AND block_number = ANY($2::bigint[])", chain_name, sorted({r[3] for r in raw_rows}))}
    return {"chain": chain_name, **await decode(conn, chain_name, raw_rows, known, tokens)}


async def held(source, holder: str, underlying: str, decimals: int) -> Decimal:
    """Underlying actually held by a contract — the only honest TVL measure,
    since private transfers strand tokens and inflate totalSupply."""
    if underlying == NATIVE:
        raw = await source.native_balance(holder)
    else:
        raw = _hexint(await source.eth_call(underlying, SEL_BALANCE_OF + _word_arg(holder)))
    return _scaled(raw, decimals)


async def snapshot_tvl(conn, source, chain_name: str, version: str, holdings: list[dict],
                       taken_at: datetime | None = None) -> int:
    """Record what each holder owns right now. Best-effort: a failed read is
    skipped rather than written as zero, because a zero would read as a crash."""
    taken_at = taken_at or datetime.now(timezone.utc)
    rows = []
    for h in holdings:
        try:
            amount = await held(source, h["holder"], h["underlying"], h["decimals"])
        except Exception as e:  # noqa: BLE001
            log.warning("%s %s %s: TVL read failed (%s)", version, chain_name, h["token"], e)
            continue
        rows.append((taken_at, version, chain_name, h["token"], h["holder"], amount))
    if conn is not None and rows:
        await conn.executemany(
            "INSERT INTO zerc20.tvl_snapshot (taken_at, version, chain, token, holder, amount) "
            "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING", rows)
    return len(rows)


async def v1_holdings(source, managers: dict[str, str]) -> list[dict]:
    """v1 TVL holders: each LiquidityManager, with its underlying read on-chain."""
    out = []
    for lm, token in managers.items():
        underlying = _addr(await source.eth_call(lm, SEL_UNDERLYING_TOKEN))
        decimals = 6 if token == "zUSDC" else 18
        out.append({"holder": lm, "token": token, "underlying": underlying, "decimals": decimals})
    return out


def v2_holdings(tokens: list[dict]) -> list[dict]:
    """v2 TVL holders: each zToken holds its own underlying."""
    return [{"holder": t["ztoken"], "token": t["symbol"], "underlying": t["underlying"],
             "decimals": t["decimals"]} for t in tokens]


async def sync(pool, source, chain_name: str, cfg, *, chunk: int, backfill: bool = False,
               last_blocks: int | None = None) -> dict:
    """Index v2 for one chain: refresh the registry, scan logs, snapshot TVL.

    v2 keeps its own cursor (`v2:<chain>`) so it advances independently of v1
    and starts at the v2 deployment block rather than at v1's history.
    """
    tip = await source.block_number()
    safe_tip = tip - cfg.confirmations
    conn = None if cfg.dry_run else await pool.acquire()
    cursor_key = f"v2:{chain_name}"
    start = START_BLOCK[chain_name]
    totals = {"raw": 0, "deposits": 0, "exits": 0, "transfers": 0, "sends": 0, "withdrawals": 0,
              "merges": 0, "fees": 0, "rage_exits": 0}
    try:
        tokens = await refresh_registry(conn, source, chain_name)
        if not tokens:
            return {"chain": chain_name, "version": "v2", "note": "no v2 tokens on this chain", **totals}

        if last_blocks is not None:
            from_block = max(start, safe_tip - last_blocks)
        else:
            last = start - 1
            if conn is not None:
                row = await conn.fetchrow(
                    "SELECT last_block FROM zerc20.cursor WHERE chain = $1", cursor_key)
                if row is None:
                    await conn.execute(
                        "INSERT INTO zerc20.cursor (chain, start_block, last_block) "
                        "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING", cursor_key, start, start - 1)
                else:
                    last = row["last_block"]
            from_block = start if backfill else max(last + 1, start)

        windows = 0
        if from_block <= safe_tip:
            async for logs, window_end in source.get_logs(addresses(tokens), from_block, safe_tip, chunk):
                windows += 1
                if cfg.dry_run:
                    totals["raw"] += len(logs)
                else:
                    for key, value in (await store(conn, chain_name, logs, source, tokens)).items():
                        totals[key] = totals.get(key, 0) + value
                    await conn.execute(
                        "UPDATE zerc20.cursor SET last_block = $2, updated_at = now() WHERE chain = $1",
                        cursor_key, window_end)
                if windows % 25 == 0:
                    log.info("%s v2: at block %d/%d (%d raw logs so far)",
                             chain_name, window_end, safe_tip, totals["raw"])

        tvl_rows = 0
        if conn is not None:
            tvl_rows = await snapshot_tvl(conn, source, chain_name, "v2", v2_holdings(tokens))
        return {"chain": chain_name, "version": "v2", "from": from_block, "to": safe_tip,
                "windows": windows, "tokens": [t["symbol"] for t in tokens],
                "tvl_rows": tvl_rows, **totals}
    finally:
        if conn is not None:
            await pool.release(conn)
