"""Tests for crypto-sync++ pure logic: gas-from-receipt math, block-time USD
pricing (stablecoin peg + historical-rate cache), RPC endpoint selection, and
the small parsing helpers. No network or DB — clients/queries are faked. Async
helpers are driven with asyncio.run (no pytest-asyncio in this project)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from merge_api import crypto_sync as cs


def run(coro):
    return asyncio.run(coro)


# --- small parsing helpers --------------------------------------------------

def test_short_addr():
    assert cs._short_addr("0x1234567890abcdef1234") == "0x1234…1234"
    assert cs._short_addr(None) == "unknown"
    assert cs._short_addr("short") == "short"


def test_cutoff_and_iso_unix():
    assert cs._cutoff_unix("2026-06-01") == int(
        datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
    assert cs._iso_to_unix("2026-06-09T14:09:52.000Z") == int(
        datetime(2026, 6, 9, 14, 9, 52, tzinfo=timezone.utc).timestamp())
    assert cs._iso_to_unix("") == 0
    assert cs._iso_to_unix(None) == 0


def test_cg_id_map_covers_natives():
    for code in ("SOL", "ETH", "TRX", "BTC", "BNB", "POL"):
        assert code in cs._CG_ID


# --- RPC endpoint selection (gas receipts use the archive-capable URL) -------

def test_evm_rpc_url_alchemy_then_nodereal_then_public():
    keys = {"alchemy": "AK", "nodereal": "NK"}
    assert cs._evm_rpc_url("arbitrum", keys) == "https://arb-mainnet.g.alchemy.com/v2/AK"
    assert cs._evm_rpc_url("bsc", keys) == "https://bsc-mainnet.nodereal.io/v1/NK"
    # bsc with no nodereal key → the chain's public RPC
    assert cs._evm_rpc_url("bsc", {"alchemy": "AK"}) == cs.EVM_CHAINS["bsc"]["rpc"]
    # alchemy chain but no alchemy key → public RPC
    assert cs._evm_rpc_url("base", {}) == cs.EVM_CHAINS["base"]["rpc"]


# --- block-time USD pricing -------------------------------------------------

def test_usd_at_stablecoins_are_pegged():
    # stablecoins short-circuit to 1.0 before any pool/client use → pass None
    for code in ("USDC", "USDT", "DAI", "usd"):
        assert run(cs._usd_at(None, None, {}, code, date(2026, 6, 1))) == 1.0


def test_usd_at_uses_cache_no_fetch(monkeypatch):
    """A cached fin_fx_rate is returned without calling CoinGecko (client=None
    would raise if a fetch were attempted)."""
    async def fake_asset(pool, code, **kw):
        return "asset-eth"

    async def fake_rate_on(pool, asset_id, on_date):
        return 2500.0

    monkeypatch.setattr(cs.queries, "get_or_create_asset_by_code", fake_asset)
    monkeypatch.setattr(cs.queries, "get_fx_rate_on", fake_rate_on)
    rate = run(cs._usd_at(object(), None, {}, "ETH", date(2026, 6, 1)))
    assert rate == 2500.0


def test_usd_at_backfills_and_caches(monkeypatch):
    fetched = {"n": 0}
    cached = {}

    async def fake_asset(pool, code, **kw):
        return "asset-eth"

    async def fake_rate_on(pool, asset_id, on_date):
        return None  # not cached yet

    async def fake_cache(pool, asset_id, rate, on_date, source="coingecko_hist"):
        cached["v"] = (asset_id, rate, source)

    async def fake_hist(client, cg_id, on_date):
        fetched["n"] += 1
        return 1800.0

    monkeypatch.setattr(cs.queries, "get_or_create_asset_by_code", fake_asset)
    monkeypatch.setattr(cs.queries, "get_fx_rate_on", fake_rate_on)
    monkeypatch.setattr(cs.queries, "cache_fx_rate", fake_cache)
    monkeypatch.setattr(cs, "_coingecko_history", fake_hist)

    cache: dict = {}
    rate = run(cs._usd_at(object(), object(), cache, "ETH", date(2026, 6, 1)))
    assert rate == 1800.0
    assert cached["v"] == ("asset-eth", 1800.0, "coingecko_hist")
    # second call for the same (asset, date) hits the in-run cache, no re-fetch
    rate2 = run(cs._usd_at(object(), object(), cache, "ETH", date(2026, 6, 1)))
    assert rate2 == 1800.0
    assert fetched["n"] == 1


def test_coingecko_history_parses_price():
    class FakeResp:
        status_code = 200

        def json(self):
            return {"market_data": {"current_price": {"usd": 42.5}}}

    class FakeClient:
        async def get(self, url, params=None):
            return FakeResp()

    assert run(cs._coingecko_history(FakeClient(), "ethereum", date(2026, 6, 1))) == 42.5


def test_coingecko_history_missing_returns_none():
    class FakeResp:
        status_code = 200

        def json(self):
            return {}

    class FakeClient:
        async def get(self, url, params=None):
            return FakeResp()

    assert run(cs._coingecko_history(FakeClient(), "ethereum", date(2026, 6, 1))) is None


# --- gas as a ledger leg, derived from the tx receipt -----------------------

def _hex(n: int) -> str:
    return hex(n)


def test_evm_gas_for_hashes_books_when_we_signed(monkeypatch):
    addr = "0xMyWallet".lower()
    receipts = {
        "0xaaa": {"from": addr, "gasUsed": _hex(21000), "effectiveGasPrice": _hex(20_000_000_000)},
        "0xbbb": {"from": "0xsomeoneelse", "gasUsed": _hex(50000), "effectiveGasPrice": _hex(1)},
    }

    async def fake_rpc(client, rpc_url, method, params):
        assert method == "eth_getTransactionReceipt"
        return receipts.get(params[0])

    monkeypatch.setattr(cs, "_rpc", fake_rpc)
    rows = run(cs._evm_gas_for_hashes(object(), "rpc", addr, "arbitrum", {"0xaaa": 1000, "0xbbb": 2000}))
    # only the tx we signed (0xaaa) yields a gas row
    assert len(rows) == 1
    direction, code, dec, qty, cp, h, ref, ts = rows[0]
    assert direction == "out" and code == "ETH" and cp == "gas"
    assert ref == "arbitrum:0xaaa:gas" and ts == 1000
    assert abs(qty - (21000 * 20_000_000_000) / 1e18) < 1e-18


def test_evm_gas_legacy_tx_falls_back_to_gasprice(monkeypatch):
    addr = "0xme"
    receipt = {"from": addr, "gasUsed": _hex(21000)}  # no effectiveGasPrice (legacy)
    tx = {"gasPrice": _hex(10_000_000_000)}

    async def fake_rpc(client, rpc_url, method, params):
        if method == "eth_getTransactionReceipt":
            return receipt
        if method == "eth_getTransactionByHash":
            return tx
        raise AssertionError(method)

    monkeypatch.setattr(cs, "_rpc", fake_rpc)
    rows = run(cs._evm_gas_for_hashes(object(), "rpc", addr, "ethereum", {"0xccc": 5}))
    assert len(rows) == 1
    qty = rows[0][3]
    assert abs(qty - (21000 * 10_000_000_000) / 1e18) < 1e-18


def test_evm_gas_skips_zero_and_missing(monkeypatch):
    addr = "0xme"

    async def fake_rpc(client, rpc_url, method, params):
        h = params[0]
        if h == "0xzero":
            return {"from": addr, "gasUsed": "0x0", "effectiveGasPrice": "0x0"}
        if h == "0xmissing":
            return None
        raise AssertionError(h)

    monkeypatch.setattr(cs, "_rpc", fake_rpc)
    rows = run(cs._evm_gas_for_hashes(object(), "rpc", addr, "base", {"0xzero": 1, "0xmissing": 2}))
    assert rows == []
