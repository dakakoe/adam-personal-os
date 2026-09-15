"""Daily FX worker: fiat → USD rates for the budget module's net worth.

frankfurter.app returns base=USD with rates[X] = "X per 1 USD". Our fin_fx_rate
stores `rate` = USD per 1 unit of the asset, so we store 1/frankfurter_rate.
USD and the USD stablecoins are pinned to 1.0. Crypto (non-stablecoin) prices
are out of scope here (manual in v1; CoinGecko in Phase 2)."""
from __future__ import annotations

import asyncio
import logging
from datetime import date

import asyncpg

from .config import Config

log = logging.getLogger("fin_fx")

# treated as 1 USD regardless of the feed
_USD_PEGGED = {"USD", "USDT", "USDC", "DAI", "BUSD", "TUSD"}


async def _run_async(cfg: Config) -> int:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(cfg.fx_url)
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        log.error("FX fetch failed: %s", e)
        return 1
    rates = data.get("rates") or {}          # {THB: 32.5, RUB: 80, ...}  (per 1 USD)
    if not rates:
        log.error("FX feed returned no rates")
        return 1

    # crypto spot prices (USD per 1 unit) from CoinGecko (free, no key)
    crypto_usd: dict[str, float] = {}
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            cg = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "solana,ethereum,tron,bitcoin,binancecoin,polygon-ecosystem-token",
                        "vs_currencies": "usd"})
            cg.raise_for_status()
            j = cg.json()
        for cg_id, code in (("solana", "SOL"), ("ethereum", "ETH"), ("tron", "TRX"), ("bitcoin", "BTC"),
                            ("binancecoin", "BNB"), ("polygon-ecosystem-token", "POL")):
            px = (j.get(cg_id) or {}).get("usd")
            if px:
                crypto_usd[code] = float(px)
    except Exception as e:  # noqa: BLE001
        log.warning("CoinGecko fetch failed (crypto prices skipped): %s", e)

    pool = await asyncpg.create_pool(cfg.db_url, min_size=1, max_size=2, statement_cache_size=0)
    try:
        async with pool.acquire() as conn:
            assets = await conn.fetch(
                "SELECT id::text, upper(code) AS code FROM memory.fin_asset WHERE kind IN ('fiat','crypto')")
            today = date.today()
            n = 0
            for a in assets:
                code = a["code"]
                source = "frankfurter"
                if code in _USD_PEGGED:
                    usd_rate = 1.0
                elif code in crypto_usd:
                    usd_rate, source = crypto_usd[code], "coingecko"
                elif code in rates and rates[code]:
                    usd_rate = 1.0 / float(rates[code])
                else:
                    continue  # unknown — manual price entry
                if cfg.dry_run:
                    log.info("DRY %s → %.6f USD (%s)", code, usd_rate, source)
                    continue
                await conn.execute(
                    """
                    INSERT INTO memory.fin_fx_rate (asset_id, quote, rate, rate_date, source)
                    VALUES ($1::uuid, 'USD', $2, $3, $4)
                    ON CONFLICT (asset_id, quote, rate_date)
                    DO UPDATE SET rate = excluded.rate, source = excluded.source
                    """, a["id"], usd_rate, today, source)
                n += 1
        log.info("fin_fx: upserted %d rates for %s (%d crypto)", n, today, len(crypto_usd))
        return 0
    finally:
        await pool.close()


def run(cfg: Config) -> int:
    return asyncio.run(_run_async(cfg))
