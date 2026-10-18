# zerc20_indexer

Indexes zERC20 contract logs straight from the chains into Postgres, so the
analytics dashboard stops depending on Dune.

## Why it exists

The dashboard used two Dune queries (7429073 organic, 7485765 all wallets). They
scan raw `<chain>.logs` tables, take about two minutes, and only run on Dune's
free tier — past its 2-minute limit. Every "refresh" therefore burned credits and
still served yesterday's rows, and a daily scheduled task existed purely to
re-run them. The data itself is small: roughly 30k mint/burn events since
January across six chains.

## What it indexes

Per chain: the zAsset token contracts and their LiquidityManagers.

| Event | Why |
|---|---|
| `Transfer` (mints/burns only) | the counted operations; plain wallet-to-wallet transfers were never in scope on Dune either |
| `Teleport` | the only on-chain way to tell a **redeem** from a **wrap** — both are mints from the zero address |
| `Unwrapped` | `feeAmount`, the liquidity-curve unwrap fee actually charged |

`zerc20.raw_log` keeps every log verbatim and append-only; the decoded tables are
derived from it and can be truncated and rebuilt without touching an RPC.
`zerc20.v_daily` and `zerc20.v_daily_organic` emit the exact column shape the
dashboard already consumes (`day, chain, token, tx_type, txs, volume_usd,
unique_wallets`).

## Provider keys are not optional

Public endpoints were measured on 2026-09-20, and two chains cannot be indexed
without a key:

| Chain | Public endpoints | Needs |
|---|---|---|
| BNB Chain | refuse `eth_getLogs` at **any** range ("limit exceeded", even 50 blocks) | `NODEREAL_API_KEY` |
| Polygon | cap ranges at 1,000 blocks **and prune history** | `ALCHEMY_API_KEY` for the backfill |
| Ethereum, Arbitrum, Base | work, but slowly and with 403 throttling | `ALCHEMY_API_KEY` recommended |
| Kaia | `archive-en.node.kaia.io` serves January-era ranges | nothing |

Both keys already exist in `/srv/memory/secrets/.env` for the wallet sync.

## Environment

| Var | Meaning |
|---|---|
| `ZERC20_DATABASE_URL` | overrides the `POSTGRES_*` pieces used by the other services |
| `ALCHEMY_API_KEY`, `NODEREAL_API_KEY` | providers; see above |
| `ZERC20_START_DATE` | backfill floor, default `2026-01-01` (first activity was 2026-02-04) |
| `ZERC20_CONFIRMATIONS` | blocks held back from the tip, default 12 |
| `ZERC20_DRY_RUN` | fetch and count, write nothing |

## Commands

```bash
python -m zerc20_indexer                          # follow the tip (what the timer runs)
python -m zerc20_indexer --backfill               # re-scan from each chain's start block
python -m zerc20_indexer --chain Polygon --backfill
python -m zerc20_indexer --dry-run --last 40000   # smoke test, no database needed
```

`--backfill` is idempotent: raw logs are keyed by `(chain, tx_hash, log_index)`
and conflicts are ignored, so a crashed backfill is resumed by running it again.

## Cutover

Dune and the indexer run side by side until their daily rows agree:

```bash
python scripts/zerc20_verify.py --dune dune_rows.json          # all wallets
python scripts/zerc20_verify.py --dune dune_clean.json --organic
```

Only when that reconciles does the dashboard switch, and only then is the
`zerc20-dune-refresh` task retired.
