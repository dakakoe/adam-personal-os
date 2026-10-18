"""Crypto wallet balance sync → holdings snapshots.

For each fin_account with a wallet_address + chain, read on-chain balances
(native coin + well-known stablecoins) via public RPC/indexer endpoints and
upsert fin_holding rows (source='chain'). Token coverage is intentionally
scoped to the assets the user actually holds (SOL/ETH/TRX + USDC/USDT); unknown
SPL/ERC-20 tokens are skipped rather than guessed. Prices come from the fin_fx
worker (CoinGecko), not here."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from . import queries

log = logging.getLogger("crypto_sync")

SOLANA_RPC = "https://api.mainnet-beta.solana.com"
TRON_API = "https://api.trongrid.io"

# Alchemy getAssetTransfers — full archive history, native+ERC-20+internal in one
# paginated call. Covers every EVM chain we need EXCEPT BSC (not supported by the
# Transfers API), which falls back to NodeReal archive getLogs.
_ALCHEMY_NET = {
    "ethereum": "eth-mainnet", "base": "base-mainnet", "arbitrum": "arb-mainnet",
    "optimism": "opt-mainnet", "polygon": "polygon-mainnet",
}
_ALCHEMY_INTERNAL = {"ethereum", "polygon"}   # 'internal' category only on these
HELIUS_TX_API = "https://api.helius.xyz/v0/addresses"

# known token mints/contracts → (code, decimals)
SOL_TOKENS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", 6),
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", 6),
}
TRON_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"   # USDT-TRC20, 6 decimals

# EVM chains share the same address + JSON-RPC; only the endpoint, native coin,
# and well-known stablecoin contracts differ. Free public RPC = publicnode.com.
EVM_CHAINS = {
    "ethereum": {"rpc": "https://ethereum-rpc.publicnode.com", "native": "ETH", "tokens": {
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
        "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
        "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18)}},
    "arbitrum": {"rpc": "https://arbitrum-one-rpc.publicnode.com", "native": "ETH", "tokens": {
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": ("USDC", 6),
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": ("USDT", 6),
        "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1": ("DAI", 18)}},
    "base": {"rpc": "https://base-rpc.publicnode.com", "native": "ETH", "tokens": {
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", 6),
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb": ("DAI", 18)}},
    "optimism": {"rpc": "https://optimism-rpc.publicnode.com", "native": "ETH", "tokens": {
        "0x0b2c639c533813f4aa9d7837caf62653d097ff85": ("USDC", 6),
        "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58": ("USDT", 6),
        "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1": ("DAI", 18)}},
    "bsc": {"rpc": "https://bsc-rpc.publicnode.com", "native": "BNB", "tokens": {
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": ("USDC", 18),
        "0x55d398326f99059ff775485246999027b3197955": ("USDT", 18),    # BSC stables are 18-decimal
        "0x1af3f329e8be154074d8769d1ffa4ee058b1dbc3": ("DAI", 18)}},
    "polygon": {"rpc": "https://polygon-bor-rpc.publicnode.com", "native": "POL", "tokens": {
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": ("USDC", 6),
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": ("USDT", 6),
        "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063": ("DAI", 18)}},
}
EVM_ALL = "evm"   # a wallet on chain='evm' is scanned across every EVM chain above
# friendly aliases people might enter (NOT 'evm' — that means all-chains, below)
_EVM_ALIAS = {"eth": "ethereum", "bnb": "bsc",
              "binance": "bsc", "binance-smart-chain": "bsc", "matic": "polygon",
              "arb": "arbitrum", "op": "optimism"}

_STABLE = {"USDC", "USDT", "DAI"}
# Address-poisoning scammers send near-zero amounts of REAL stablecoins (via the
# genuine contract, so a contract allowlist can't catch them) to plant lookalike
# counterparties in your history. A $1-token transfer below this is noise, never
# a real payment — drop it from the activity feed.
_STABLE_DUST = 0.01

# CoinGecko ids for the native coins we price historically (mirrors fin_fx).
# Stablecoins are pegged to 1.0 and never hit CoinGecko.
_CG_ID = {"SOL": "solana", "ETH": "ethereum", "TRX": "tron", "BTC": "bitcoin",
          "BNB": "binancecoin", "POL": "polygon-ecosystem-token"}
# gas lands as an expense leg under this category (seeded by the migration)
_GAS_CATEGORY = "network-fees"


async def _coingecko_history(client, cg_id: str, on_date) -> float | None:
    """USD price of a coin on a given date via CoinGecko's free daily-history
    endpoint (DD-MM-YYYY). Returns None on any failure — pricing is best-effort,
    a missing rate just leaves usd_value NULL."""
    d = on_date.strftime("%d-%m-%Y")
    url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/history"
    params = {"date": d, "localization": "false"}
    try:
        r = await client.get(url, params=params)
        if r.status_code == 429:            # rate-limited → back off once and retry
            await asyncio.sleep(2.0)
            r = await client.get(url, params=params)
        px = (((r.json() or {}).get("market_data") or {}).get("current_price") or {}).get("usd")
        return float(px) if px else None
    except Exception:  # noqa: BLE001
        return None


async def _usd_at(pool, client, cache: dict, code: str, on_date, *,
                  decimals: int = 18) -> float | None:
    """USD-per-unit for `code` on `on_date`. Stablecoins → 1.0; native coins use
    the fin_fx_rate cache, backfilled once from CoinGecko. Per-run `cache` keys on
    (asset_id, date) so a date is fetched at most once per sync."""
    code = code.upper()
    if code in _STABLE or code == "USD":
        return 1.0
    asset_id = await queries.get_or_create_asset_by_code(
        pool, code, kind="crypto", decimals=decimals, chain=None)
    key = (asset_id, on_date)
    if key in cache:
        return cache[key]
    rate = await queries.get_fx_rate_on(pool, asset_id, on_date)
    if rate is None:
        cg_id = _CG_ID.get(code)
        if cg_id:
            rate = await _coingecko_history(client, cg_id, on_date)
            if rate is not None:
                await queries.cache_fx_rate(pool, asset_id, rate, on_date)
                await asyncio.sleep(0.05)   # be gentle on the free tier
    cache[key] = rate
    return rate


async def _solana(client, addr: str) -> list[tuple[str, str, int, float]]:
    """[(code, kind, decimals, quantity)] for a Solana wallet."""
    out: list[tuple[str, str, int, float]] = []
    bal = await client.post(SOLANA_RPC, json={
        "jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [addr]})
    lam = (bal.json().get("result") or {}).get("value", 0)
    out.append(("SOL", "crypto", 9, lam / 1e9))
    toks = await client.post(SOLANA_RPC, json={
        "jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
        "params": [addr, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                   {"encoding": "jsonParsed"}]})
    for acc in (toks.json().get("result") or {}).get("value", []):
        info = acc["account"]["data"]["parsed"]["info"]
        mint = info.get("mint")
        amt = info.get("tokenAmount", {}).get("uiAmount") or 0
        if mint in SOL_TOKENS and amt:
            code, dec = SOL_TOKENS[mint]
            out.append((code, "crypto", dec, float(amt)))
    return out


async def _tron(client, addr: str) -> list[tuple[str, str, int, float]]:
    out: list[tuple[str, str, int, float]] = []
    r = await client.get(f"{TRON_API}/v1/accounts/{addr}")
    data = (r.json().get("data") or [{}])
    acc = data[0] if data else {}
    out.append(("TRX", "crypto", 6, (acc.get("balance") or 0) / 1e6))
    for entry in acc.get("trc20", []) or []:
        for contract, raw in entry.items():
            if contract == TRON_USDT:
                out.append(("USDT", "crypto", 6, int(raw) / 1e6))
    return out


def _evm_balanceof_data(addr: str) -> str:
    return "0x70a08231" + "000000000000000000000000" + addr.lower().replace("0x", "")


async def _evm(client, addr: str, chain: str) -> list[tuple[str, str, int, float]]:
    cfg = EVM_CHAINS[chain]
    rpc = cfg["rpc"]
    out: list[tuple[str, str, int, float]] = []
    bal = await client.post(rpc, json={
        "jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [addr, "latest"]})
    wei = int(bal.json().get("result", "0x0"), 16)
    out.append((cfg["native"], "crypto", 18, wei / 1e18))
    for contract, (code, dec) in cfg["tokens"].items():
        try:
            r = await client.post(rpc, json={
                "jsonrpc": "2.0", "id": 1, "method": "eth_call",
                "params": [{"to": contract, "data": _evm_balanceof_data(addr)}, "latest"]})
            raw = int(r.json().get("result") or "0x0", 16)
            if raw:
                out.append((code, "crypto", dec, raw / (10 ** dec)))
        except Exception:  # noqa: BLE001
            continue
    return out


async def sync_wallet(pool, account: dict) -> dict:
    """Read on-chain balances → holdings (each tagged with its chain). A wallet
    on chain='evm' is scanned across EVERY EVM chain (Metamask-style); 'solana'/
    'tron' scan that one chain. Holdings are replaced wholesale each sync."""
    import httpx
    chain = (account.get("chain") or "").lower()
    chain = _EVM_ALIAS.get(chain, chain)
    addr = account.get("wallet_address")
    if not addr:
        return {"account": account["name"], "error": "no address"}

    # gather (code, kind, decimals, qty, source_chain) across the relevant chains
    gathered: list[tuple[str, str, int, float, str]] = []
    chains_hit, chains_failed = 0, 0
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            if chain == "solana":
                for c, k, d, q in await _solana(client, addr):
                    if q > 0:
                        gathered.append((c, k, d, q, "solana"))
                chains_hit = 1
            elif chain == "tron":
                for c, k, d, q in await _tron(client, addr):
                    if q > 0:
                        gathered.append((c, k, d, q, "tron"))
                chains_hit = 1
            elif chain in EVM_CHAINS or chain == EVM_ALL:
                targets = list(EVM_CHAINS) if chain == EVM_ALL else [chain]
                for ch in targets:
                    try:
                        for c, k, d, q in await _evm(client, addr, ch):
                            if q > 0:
                                gathered.append((c, k, d, q, ch))
                        chains_hit += 1
                    except Exception as e:  # noqa: BLE001 — one chain's RPC blip shouldn't kill the rest
                        log.warning("evm sync failed %s/%s: %s", account["name"], ch, e)
                        chains_failed += 1
            else:
                return {"account": account["name"], "error": f"unsupported chain {chain}"}
    except Exception as e:  # noqa: BLE001
        log.warning("sync failed for %s (%s): %s", account["name"], chain, e)
        return {"account": account["name"], "error": str(e)[:120]}

    # replace this account's chain holdings with the fresh snapshot
    await queries.clear_chain_holdings(pool, account["id"])
    for code, kind, decimals, qty, src_chain in gathered:
        # assets stay chain-agnostic (one ETH, one USDT, …); the holding row
        # carries which chain it sits on, so the portfolio shows ETH·Arbitrum etc.
        asset_id = await queries.get_or_create_asset_by_code(
            pool, code, kind=kind, decimals=decimals, chain=None)
        await queries.upsert_fin_holding(
            pool, account_id=account["id"], asset_id=asset_id, quantity=qty,
            source="chain", chain=src_chain)
    out = {"account": account["name"], "chain": chain, "assets": len(gathered), "chains": chains_hit}
    if chains_failed:
        out["chains_failed"] = chains_failed
    return out


# --- transfer ingestion (operational wallets, since cutoff) ---------
#
# Balances stay holdings-as-truth (synced above). Transfers are written as
# single-leg fin_transactions purely as a categorizable activity feed — the
# balance view ignores them, so gas/swaps/untracked tokens can't desync the
# balance. Coverage mirrors the balance sync: native coin + USDC/USDT/DAI,
# which also keeps scam-airdrop tokens out of the ledger.

def _short_addr(a: str | None) -> str:
    if not a:
        return "unknown"
    a = a.strip()
    return f"{a[:6]}…{a[-4:]}" if len(a) > 12 else a


def _cutoff_unix(cutoff: str) -> int:
    """'2026-06-01' → unix seconds (UTC midnight)."""
    return int(datetime.strptime(cutoff, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _iso_to_unix(s: str) -> int:
    """Alchemy metadata blockTimestamp '2026-06-09T14:09:52.000Z' → unix seconds."""
    try:
        return int(datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return 0


async def _alchemy_transfers_page(client, net: str, api_key: str, params: dict) -> dict:
    """One getAssetTransfers page, retrying on Alchemy's free-tier rate limit.
    A 429 arrives either as an HTTP status or a JSON-RPC error {code:429}; the
    bursty multi-chain sweep trips the per-second compute-unit cap, so back off
    (exponential) and retry rather than failing the whole chain's sync."""
    url = f"https://{net}.g.alchemy.com/v2/{api_key}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "alchemy_getAssetTransfers", "params": [params]}
    last = "rate-limited"
    for attempt in range(4):
        r = await client.post(url, json=body)
        if r.status_code == 429:
            last = "http 429"
            await asyncio.sleep(1.5 * (attempt + 1))
            continue
        j = r.json()
        err = j.get("error")
        if err:
            if isinstance(err, dict) and err.get("code") == 429 and attempt < 3:
                last = str(err)[:90]
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(str(err)[:90])
        return j.get("result") or {}
    raise RuntimeError(f"alchemy rate-limited after retries ({last})")


async def _evm_transfers_alchemy(client, addr: str, ch: str, cutoff_unix: int, api_key: str):
    """Transfers for one EVM chain via Alchemy getAssetTransfers — full archive
    history, native + ERC-20 + internal in one paginated call. ERC-20 restricted
    to our tracked stablecoins by CONTRACT address (Alchemy returns scam tokens
    with lookalike symbols but distinct contracts)."""
    cfg = EVM_CHAINS[ch]
    net = _ALCHEMY_NET[ch]
    lower = addr.lower()
    cats = ["external", "erc20"] + (["internal"] if ch in _ALCHEMY_INTERNAL else [])
    out: list[tuple] = []
    # one sweep per direction: fromAddress = sent, toAddress = received
    for direction, key in (("out", "fromAddress"), ("in", "toAddress")):
        page = None
        pages = 0
        while pages < 200:                 # 200 × 1000 = ample for full history
            params = {"fromBlock": "0x0", "toBlock": "latest", key: addr,
                      "category": cats, "withMetadata": True, "excludeZeroValue": True,
                      "maxCount": "0x3e8", "order": "desc"}
            if page:
                params["pageKey"] = page
            res = await _alchemy_transfers_page(client, net, api_key, params)
            stop = False
            for t in (res.get("transfers") or []):
                ts = _iso_to_unix((t.get("metadata") or {}).get("blockTimestamp") or "")
                if ts and ts < cutoff_unix:
                    stop = True            # desc order → everything past here is older
                    break
                cat = t.get("category")
                if cat in ("external", "internal"):
                    code, dec = cfg["native"], 18
                else:                       # erc20 — only our tracked stablecoins
                    contract = ((t.get("rawContract") or {}).get("address") or "").lower()
                    known = cfg["tokens"].get(contract)
                    if not known:
                        continue
                    code, dec = known
                val = t.get("value")
                if val is None:
                    continue
                qty = float(val)
                if qty == 0 or (code in _STABLE and qty < _STABLE_DUST):
                    continue
                cp = (t.get("to") if direction == "out" else t.get("from")) or "unknown"
                h = t.get("hash") or ""
                uid = t.get("uniqueId") or f"{h}:{cat}"
                out.append((direction, code, dec, qty, cp, h, f"{ch}:{uid}", ts))
            page = res.get("pageKey")
            pages += 1
            if stop or not page:
                break
            await asyncio.sleep(0.05)
    return out


# Etherscan V2 chain ids. Free tier serves ETH/Arbitrum/Polygon (+others);
# Base/Optimism return NOTOK "Free API access is not supported for this chain".
_ETHERSCAN_CHAINID = {"ethereum": 1, "arbitrum": 42161, "polygon": 137, "optimism": 10, "base": 8453}


async def _evm_transfers_etherscan(client, addr: str, ch: str, cutoff_unix: int, key: str):
    """Address token + native transfers via Etherscan V2 (module=account). This is
    the free workhorse: a purpose-built address-history index, so it sidesteps the
    getLogs 10-block/archive caps and the enhanced-Transfers-API throttling that
    block the Alchemy/publicnode free tiers. Free tier covers ETH/Arbitrum/Polygon;
    unsupported chains return NOTOK → empty (no assets there anyway)."""
    cid = _ETHERSCAN_CHAINID.get(ch)
    if not cid:
        raise RuntimeError(f"no etherscan chainid for {ch}")
    cfg = EVM_CHAINS[ch]
    lower = addr.lower()
    out: list[tuple] = []
    for action, is_token in (("tokentx", True), ("txlist", False)):
        page = 1
        while page <= 10:
            r = await client.get("https://api.etherscan.io/v2/api", params={
                "chainid": cid, "module": "account", "action": action, "address": addr,
                "sort": "desc", "page": page, "offset": 1000, "apikey": key})
            j = r.json()
            res = j.get("result")
            if j.get("status") != "1" or not isinstance(res, list):
                break   # NOTOK (unsupported chain / rate) or no rows → stop this action
            stop = False
            for t in res:
                ts = int(t.get("timeStamp") or 0)
                if ts and ts < cutoff_unix:
                    stop = True
                    break
                t_from, t_to = (t.get("from") or "").lower(), (t.get("to") or "").lower()
                if lower not in (t_from, t_to):
                    continue
                if is_token:
                    known = cfg["tokens"].get((t.get("contractAddress") or "").lower())
                    if not known:      # only our tracked stablecoins (skip scam tokens)
                        continue
                    code, dec = known
                else:
                    code, dec = cfg["native"], 18
                qty = int(t.get("value") or 0) / (10 ** dec)
                if qty == 0 or (code in _STABLE and qty < _STABLE_DUST):
                    continue
                direction = "out" if t_from == lower else "in"
                cp = t_to if direction == "out" else t_from
                h = t.get("hash") or ""
                ref = f"{ch}:{h}:{(t.get('contractAddress') or 'native')[-8:]}:{t.get('value')}"
                out.append((direction, code, dec, qty, cp, h, ref, ts))
            if stop or len(res) < 1000:
                break
            page += 1
            await asyncio.sleep(0.25)   # etherscan free ≈ 5 req/s
    return out


# --- RPC fallback for chains the free Etherscan tier doesn't cover -----
# ERC-20 Transfer(address,address,uint256) — topic0; from/to are indexed topics.
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_GETLOGS_SPAN = 45_000          # NodeReal/publicnode cap eth_getLogs at ≤50k blocks
_GETLOGS_MAX_CHUNKS = 400       # backstop so a far-back cutoff can't run away


async def _rpc(client, rpc_url: str, method: str, params):
    r = await client.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    j = r.json()
    if j.get("error"):
        raise RuntimeError(str(j["error"])[:90])
    return j.get("result")


def _topic_addr(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower().replace("0x", "")


async def _evm_transfers_rpc(client, addr: str, ch: str, cutoff_unix: int, rpc_url: str | None = None):
    """Token-transfer scan via raw eth_getLogs — used for BSC (Alchemy's Transfers
    API doesn't cover it). ERC-20 only: native value transfers aren't logs.
    `rpc_url` overrides the chain's default RPC (e.g. NodeReal archive for BSC, so
    history isn't pruned). The per-call block-range cap is discovered at runtime."""
    cfg = EVM_CHAINS[ch]
    rpc = rpc_url or cfg["rpc"]
    lower = addr.lower()
    contracts = list(cfg["tokens"])             # only the stablecoins we track
    if not contracts:
        return []
    latest = int(await _rpc(client, rpc, "eth_blockNumber", []), 16)

    a_topic = _topic_addr(addr)
    block_ts: dict[int, int] = {}
    out: list[tuple] = []

    async def block_time(bn: int) -> int:
        if bn not in block_ts:
            b = await _rpc(client, rpc, "eth_getBlockByNumber", [hex(bn), False])
            block_ts[bn] = int(b["timestamp"], 16) if b else 0
        return block_ts[bn]

    # Scan NEWEST-first and stop once we cross the cutoff. This keeps recent
    # transfers within the chunk budget regardless of how far back the cutoff is
    # (an ascending scan from genesis would exhaust the cap before reaching today
    # on a high-block chain like BSC). The per-call block-range cap is provider-
    # AND chain-specific (the RPC states its limit in the error → shrink to fit).
    span = _GETLOGS_SPAN
    hi = latest
    chunks = 0
    while hi > 0 and chunks < _GETLOGS_MAX_CHUNKS:
        lo = max(0, hi - span)
        try:
            chunk_out: list[tuple] = []
            # incoming (addr is `to`) and outgoing (addr is `from`)
            for topics in ([_TRANSFER_TOPIC, None, a_topic], [_TRANSFER_TOPIC, a_topic]):
                logs = await _rpc(client, rpc, "eth_getLogs", [{
                    "fromBlock": hex(lo), "toBlock": hex(hi),
                    "address": contracts, "topics": topics}]) or []
                await asyncio.sleep(0.1)
                for lg in logs:
                    contract = (lg.get("address") or "").lower()
                    known = cfg["tokens"].get(contract)
                    if not known or len(lg.get("topics") or []) < 3:
                        continue
                    code, dec = known
                    t_from = "0x" + lg["topics"][1][-40:]
                    t_to = "0x" + lg["topics"][2][-40:]
                    if lower not in (t_from, t_to):
                        continue
                    direction = "out" if t_from == lower else "in"
                    cp = t_to if direction == "out" else t_from
                    val = int(lg.get("data") or "0x0", 16)
                    qty = val / (10 ** dec)
                    if val == 0 or (code in _STABLE and qty < _STABLE_DUST):
                        continue
                    bn = int(lg["blockNumber"], 16)
                    ts = await block_time(bn)
                    if ts and ts < cutoff_unix:
                        continue
                    h = lg.get("transactionHash") or ""
                    li = int(lg.get("logIndex") or "0x0", 16)
                    chunk_out.append((direction, code, dec, qty, cp, h, f"{ch}:{h}:{li}", ts))
        except RuntimeError as e:
            msg = str(e)
            m = re.search(r"(?:maximum block range|limit)[:\s]+(\d+)", msg)
            if m and int(m.group(1)) < span:
                span = max(1000, int(m.group(1)))
                continue   # retry the SAME window with the provider's real cap
            # "logs count exceeds the limit" / "response size" with no parsable
            # number → just halve and retry (address-filtered queries rarely hit this)
            if ("exceed" in msg or "limit" in msg or "too large" in msg) and span > 1000:
                span = max(1000, span // 2)
                continue
            raise
        out += chunk_out
        # stop once the bottom of this window is already older than the cutoff
        if lo == 0 or (await block_time(lo)) < cutoff_unix:
            break
        hi = lo - 1
        chunks += 1
    return out


# --- gas as ledger legs (crypto-sync++) -------------------------------------
# Every tx we SIGN burns native coin as gas. The transfer feeds above never see
# it (Alchemy/getLogs report token/native movements, not fees), so we fetch the
# receipt for each tx we originated and record gas as a single-leg native-coin
# outflow under the 'network-fees' category. Deduped per txhash (one tx = one
# gas charge, even when it moved several tokens) and gated on receipt.from ==
# our address so we never book gas for a tx someone else paid for.

def _evm_rpc_url(ch: str, keys: dict) -> str:
    """Best archive-capable JSON-RPC endpoint for receipts on this chain."""
    if ch in _ALCHEMY_NET and keys.get("alchemy"):
        return f"https://{_ALCHEMY_NET[ch]}.g.alchemy.com/v2/{keys['alchemy']}"
    if ch == "bsc" and keys.get("nodereal"):
        return f"https://bsc-mainnet.nodereal.io/v1/{keys['nodereal']}"
    return EVM_CHAINS[ch]["rpc"]


async def _evm_gas_for_hashes(client, rpc_url: str, addr: str, ch: str,
                              hash_ts: dict[str, int]) -> list[tuple]:
    """Gas outflow tuples for the txs we originated. `hash_ts` maps each
    candidate txhash → its block timestamp (taken from the transfer we already
    saw, so no extra block lookup). Returns rows shaped like transfers, with the
    counterparty set to 'gas' and a ':gas' source_ref suffix."""
    native = EVM_CHAINS[ch]["native"]
    lower = addr.lower()
    out: list[tuple] = []
    for h, ts in hash_ts.items():
        if not h:
            continue
        try:
            rcpt = await _rpc(client, rpc_url, "eth_getTransactionReceipt", [h])
        except Exception:  # noqa: BLE001 — one missing receipt shouldn't abort gas
            continue
        if not rcpt or (rcpt.get("from") or "").lower() != lower:
            continue                    # we didn't sign it → we didn't pay gas
        gas_used = int(rcpt.get("gasUsed") or "0x0", 16)
        eff = rcpt.get("effectiveGasPrice")
        if eff is None:                 # legacy (pre-1559) tx → gasPrice on the tx
            try:
                tx = await _rpc(client, rpc_url, "eth_getTransactionByHash", [h])
                eff = (tx or {}).get("gasPrice")
            except Exception:  # noqa: BLE001
                eff = None
        wei = gas_used * int(eff or "0x0", 16)
        if wei <= 0:
            continue
        out.append(("out", native, 18, wei / 1e18, "gas", h, f"{ch}:{h}:gas", ts))
        await asyncio.sleep(0.03)
    return out


async def _tron_transfers(client, addr: str, cutoff_unix: int):
    """TRC20 (USDT) transfers for a Tron wallet since the cutoff, paginated to full
    history via TronGrid's `fingerprint`. Native TRX is skipped — TronGrid returns
    hex addresses there; the common case is USDT-TRC20."""
    out: list[tuple] = []
    min_ms = cutoff_unix * 1000
    fingerprint = None
    pages = 0
    while pages < 200:                       # 200 × 200 = full history headroom
        params = {"min_timestamp": min_ms, "limit": 200, "only_confirmed": "true",
                  "contract_address": TRON_USDT}
        if fingerprint:
            params["fingerprint"] = fingerprint
        r = await client.get(f"{TRON_API}/v1/accounts/{addr}/transactions/trc20", params=params)
        body = r.json()
        rows = body.get("data") or []
        for t in rows:
            frm, to = t.get("from"), t.get("to")
            if addr not in (frm, to):
                continue
            direction = "out" if frm == addr else "in"
            cp = to if direction == "out" else frm
            dec = int((t.get("token_info") or {}).get("decimals") or 6)
            val = int(t.get("value") or "0")
            qty = val / (10 ** dec)
            if val == 0 or qty < _STABLE_DUST:   # USDT-TRC20 is a stablecoin
                continue
            h = t.get("transaction_id") or ""
            ts = int((t.get("block_timestamp") or 0)) // 1000
            out.append((direction, "USDT", dec, qty, cp, h, f"tron:{h}:usdt", ts))
        fingerprint = (body.get("meta") or {}).get("fingerprint")
        pages += 1
        if not fingerprint or len(rows) < 200:
            break
        await asyncio.sleep(0.1)
    return out


async def _solana_transfers(client, addr: str, cutoff_unix: int, api_key: str):
    """SOL + known SPL (USDC/USDT) transfers via Helius, paginating until the cutoff."""
    out: list[tuple] = []
    before = None
    pages = 0
    while pages < 200:   # full-history headroom (200 × 100 txs), stops at cutoff
        params = {"api-key": api_key, "limit": 100}
        if before:
            params["before"] = before
        r = await client.get(f"{HELIUS_TX_API}/{addr}/transactions", params=params)
        txs = r.json()
        if not isinstance(txs, list) or not txs:
            break
        for t in txs:
            ts = t.get("timestamp") or 0
            if ts < cutoff_unix:
                return out
            sig = t.get("signature") or ""
            # gas: Helius reports the fee + payer inline, so no extra lookup
            fee = int(t.get("fee") or 0)
            if fee > 0 and t.get("feePayer") == addr:
                out.append(("out", "SOL", 9, fee / 1e9, "gas", sig, f"solana:{sig}:gas", ts))
            for nt in (t.get("nativeTransfers") or []):
                frm, to = nt.get("fromUserAccount"), nt.get("toUserAccount")
                if addr not in (frm, to):
                    continue
                lamports = int(nt.get("amount") or 0)
                if lamports == 0:
                    continue
                direction = "out" if frm == addr else "in"
                cp = to if direction == "out" else frm
                out.append((direction, "SOL", 9, lamports / 1e9, cp, sig, f"solana:{sig}:sol:{lamports}", ts))
            for tt in (t.get("tokenTransfers") or []):
                mint = tt.get("mint")
                known = SOL_TOKENS.get(mint)
                if not known:
                    continue
                code, dec = known
                frm, to = tt.get("fromUserAccount"), tt.get("toUserAccount")
                if addr not in (frm, to):
                    continue
                amt = float(tt.get("tokenAmount") or 0)
                if amt == 0 or (code in _STABLE and amt < _STABLE_DUST):
                    continue
                direction = "out" if frm == addr else "in"
                cp = to if direction == "out" else frm
                out.append((direction, code, dec, amt, cp, sig, f"solana:{sig}:{code}:{amt}", ts))
        before = txs[-1].get("signature")
        pages += 1
        if len(txs) < 100:
            break
    return out


async def sync_wallet_transactions(pool, account: dict, cutoff: str, keys: dict) -> dict:
    """Ingest in/out transfers for one operational crypto wallet since `cutoff`
    (YYYY-MM-DD). Each chain is isolated so one provider's failure doesn't block
    the others. Returns counts; balances are untouched (holdings remain truth)."""
    import httpx
    chain = (account.get("chain") or "").lower()
    chain = _EVM_ALIAS.get(chain, chain)
    addr = account.get("wallet_address")
    if not addr:
        return {"account": account["name"], "transfers": 0}

    cutoff_unix = _cutoff_unix(cutoff)

    # INCREMENTAL: after the first backfill, only sweep back to just before the
    # newest transfer we already have on each chain (a 2-day overlap keeps
    # same-day/late-indexed rows; the idempotent source_ref makes the re-fetch a
    # no-op). This keeps each run a tiny, cheap call instead of re-sweeping full
    # archive every time — which is what was spiking past Alchemy's 500 CU/s cap.
    async with pool.acquire() as conn:
        _wm = await conn.fetch(
            "SELECT COALESCE(tags[1],'') AS chain, max(txn_date) AS d "
            "  FROM memory.fin_transaction "
            " WHERE source_kind='chain_tx' AND deleted_at IS NULL "
            "   AND (outflow_account_id=$1::uuid OR inflow_account_id=$1::uuid) "
            " GROUP BY 1", account["id"])
    watermark = {r["chain"]: r["d"] for r in _wm if r["chain"] and r["d"]}

    def _since(ch: str) -> int:
        d = watermark.get(ch)
        if not d:
            return cutoff_unix   # first run for this chain → full backfill to cutoff
        wm = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) - 2 * 86400
        return max(cutoff_unix, wm)

    # (direction, code, decimals, qty, counterparty, txhash, ref) per transfer
    gathered: list[tuple] = []
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
        if chain in EVM_CHAINS or chain == EVM_ALL:
            targets = list(EVM_CHAINS) if chain == EVM_ALL else [chain]
            for ch in targets:
                try:
                    if keys.get("etherscan") and ch in _ETHERSCAN_CHAINID:
                        # Etherscan tokentx is the free workhorse for address history —
                        # Alchemy's enhanced Transfers API is throttled and its getLogs is
                        # 10-block-capped on the free tier, and publicnode won't serve
                        # archive. Covers ETH/Arbitrum/Polygon (Base/OP → empty).
                        ch_tx = await _evm_transfers_etherscan(client, addr, ch, _since(ch), keys["etherscan"])
                    elif ch in _ALCHEMY_NET and keys.get("alchemy"):
                        ch_tx = await _evm_transfers_alchemy(client, addr, ch, _since(ch), keys["alchemy"])
                    else:
                        # BSC (no Alchemy Transfers support) → archive getLogs.
                        # NodeReal avoids the log-pruning that breaks public RPCs.
                        rpc_url = None
                        if ch == "bsc" and keys.get("nodereal"):
                            rpc_url = f"https://bsc-mainnet.nodereal.io/v1/{keys['nodereal']}"
                        ch_tx = await _evm_transfers_rpc(client, addr, ch, _since(ch), rpc_url)
                    gathered += ch_tx
                    # gas for the txs we originated on this chain (deduped per hash)
                    hash_ts: dict[str, int] = {}
                    for direction, *_rest, h, _ref, ts in ch_tx:
                        if direction == "out" and h:
                            hash_ts.setdefault(h, ts)
                    if hash_ts:
                        gathered += await _evm_gas_for_hashes(
                            client, _evm_rpc_url(ch, keys), addr, ch, hash_ts)
                except Exception as e:  # noqa: BLE001 — isolate per chain
                    log.warning("evm tx sync %s/%s: %s", account["name"], ch, e)
                    errors.append(f"{ch}:{str(e)[:60]}")
                await asyncio.sleep(0.4)   # space out chains → stay under the CU/sec cap
        elif chain == "tron":
            try:
                gathered += await _tron_transfers(client, addr, _since("tron"))
            except Exception as e:  # noqa: BLE001
                log.warning("tron tx sync %s: %s", account["name"], e)
                errors.append(f"tron:{str(e)[:60]}")
        elif chain == "solana":
            if not keys.get("helius"):
                return {"account": account["name"], "transfers": 0, "skipped": "no helius key"}
            try:
                gathered += await _solana_transfers(client, addr, _since("solana"), keys["helius"])
            except Exception as e:  # noqa: BLE001
                log.warning("solana tx sync %s: %s", account["name"], e)
                errors.append(f"solana:{str(e)[:60]}")
        else:
            return {"account": account["name"], "transfers": 0, "skipped": f"chain {chain}"}

    # price (at block time) + write each row (idempotent on source_ref), dated by
    # its on-chain block. Gas rows carry the 'network-fees' category; their USD
    # value and the native/coin USD values come from the historical-rate cache.
    added = gas_added = 0
    today = datetime.now(timezone.utc).date()
    price_cache: dict = {}
    import httpx
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as pxc:
        for direction, code, decimals, qty, cp, _h, ref, ts in gathered:
            txn_date = (datetime.fromtimestamp(ts, timezone.utc).date()
                        if ts else today)
            chain_tag = ref.split(":", 1)[0]   # ref is '<chain>:...'
            is_gas = ref.endswith(":gas")
            asset_id = await queries.get_or_create_asset_by_code(
                pool, code, kind="crypto", decimals=decimals, chain=None)
            rate = await _usd_at(pool, pxc, price_cache, code, txn_date, decimals=decimals)
            usd_value = (qty * rate) if rate is not None else None
            payee = f"Gas · {chain_tag}" if is_gas else _short_addr(cp)
            ok = await queries.insert_chain_transfer(
                pool, txn_date=txn_date, account_id=account["id"], asset_id=asset_id,
                amount=qty, direction=direction, payee_text=payee,
                note=None, source_ref=ref, chain=chain_tag, usd_value=usd_value,
                category_key=_GAS_CATEGORY if is_gas else None)
            if ok:
                added += 1
                if is_gas:
                    gas_added += 1
    out = {"account": account["name"], "transfers": added, "scanned": len(gathered)}
    if gas_added:
        out["gas"] = gas_added
    if errors:
        out["errors"] = errors
    return out


async def _stamp_health(pool, account: dict, bal: dict, tx: dict | None) -> None:
    """Record this wallet's latest sync outcome (status + counts) for the UI."""
    errors: list[str] = []
    if bal.get("error"):
        errors.append(f"balance:{bal['error']}")
    if bal.get("chains_failed"):
        errors.append(f"{bal['chains_failed']} chain(s) failed")
    if tx and tx.get("errors"):
        errors += list(tx["errors"])
    if bal.get("error"):
        status = "error"
    elif errors:
        status = "partial"
    else:
        status = "ok"
    summary = {
        "chain": account.get("chain"),
        "assets": bal.get("assets"),
        "chains": bal.get("chains"),
        "chains_failed": bal.get("chains_failed", 0),
        "transfers": (tx or {}).get("transfers", 0),
        "scanned": (tx or {}).get("scanned", 0),
        "gas": (tx or {}).get("gas", 0),
    }
    try:
        await queries.upsert_wallet_sync(
            pool, account["id"], status=status,
            error=("; ".join(errors)[:300] if errors else None), summary=summary)
    except Exception as e:  # noqa: BLE001 — health telemetry must never break a sync
        log.warning("wallet_sync stamp failed for %s: %s", account.get("name"), e)


async def sync_all_wallets(pool, cfg=None) -> dict:
    accounts = await queries.crypto_wallet_accounts(pool)
    keys = {
        "alchemy": getattr(cfg, "alchemy_api_key", None) if cfg else None,
        "nodereal": getattr(cfg, "nodereal_api_key", None) if cfg else None,
        "etherscan": getattr(cfg, "etherscan_api_key", None) if cfg else None,
        "helius": getattr(cfg, "helius_api_key", None) if cfg else None,
    }
    have_keys = any(keys.values())
    cutoff = (await queries.get_setting(pool, "crypto_tx_cutoff", "2026-06-01")
              if have_keys else None)

    results: list[dict] = []
    tx_results: list[dict] = []
    for a in accounts:
        bal = await sync_wallet(pool, a)          # balances → holdings snapshot
        results.append(bal)
        tx = None
        # Part B: in/out transfer feed + gas for OPERATIONAL crypto wallets.
        if have_keys and a.get("account_class") == "operational" and a.get("kind") in (
                "crypto_wallet", "cex", "dex"):
            tx = await sync_wallet_transactions(pool, a, cutoff, keys)
            tx_results.append(tx)
        await _stamp_health(pool, a, bal, tx)     # per-wallet sync health

    ok = [r for r in results if "error" not in r]
    out = {"wallets": len(accounts), "synced": len(ok), "results": results}
    if tx_results:
        out["transfers"] = sum(r.get("transfers", 0) for r in tx_results)
        out["gas"] = sum(r.get("gas", 0) for r in tx_results)
        out["transfer_results"] = tx_results
    return out
