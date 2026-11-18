-- migrate:up

-- zERC20 analytics, self-hosted. Replaces the two Dune queries (7429073 clean,
-- 7485765 full) that the dashboard at zerc20-dashboard.vercel.app used to read:
-- Dune's free tier times out at 2 minutes on these log scans, so "refresh"
-- burned credits and still served stale rows.
--
-- Layering mirrors the house rule (raw is truth): `raw_log` is verbatim and
-- append-only, the decoded tables are derived from it and can be truncated and
-- rebuilt, and the views carry the aggregation the dashboard consumes.
CREATE SCHEMA IF NOT EXISTS zerc20;

-- Verbatim JSON-RPC log objects for the watched contracts. Replayable: drop the
-- decoded tables, re-run the decoder, get the same rows back without touching an RPC.
CREATE TABLE zerc20.raw_log (
  chain         TEXT        NOT NULL,   -- 'Ethereum' | 'Arbitrum' | 'Base' | 'BNB Chain' | 'Polygon' | 'Kaia'
  tx_hash       TEXT        NOT NULL,
  log_index     INTEGER     NOT NULL,
  block_number  BIGINT      NOT NULL,
  address       TEXT        NOT NULL,   -- lower-cased contract that emitted it
  topic0        TEXT        NOT NULL,
  topics        TEXT[]      NOT NULL,   -- all topics incl. topic0, as 0x-hex
  data          TEXT        NOT NULL,   -- 0x-hex payload
  fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chain, tx_hash, log_index)
);
CREATE INDEX raw_log_chain_block_idx ON zerc20.raw_log (chain, block_number);
CREATE INDEX raw_log_topic0_idx      ON zerc20.raw_log (topic0);

-- Block timestamps, cached because eth_getLogs does not return them on every
-- provider. Keyed per chain; filled by batched eth_getBlockByNumber calls.
CREATE TABLE zerc20.block_time (
  chain         TEXT        NOT NULL,
  block_number  BIGINT      NOT NULL,
  block_time    TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (chain, block_number)
);

-- How far each chain is indexed. `start_block` is resolved once (deployment
-- block, or a binary search for the configured start date) and then left alone.
CREATE TABLE zerc20.cursor (
  chain        TEXT        PRIMARY KEY,
  start_block  BIGINT      NOT NULL,
  last_block   BIGINT      NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Decoded ERC-20 Transfer legs, mints and burns only (from = 0x0 or to = 0x0).
-- Ordinary wallet-to-wallet transfers are deliberately not stored: the Dune
-- queries never counted them either, so keeping the same scope keeps the
-- numbers comparable.
CREATE TABLE zerc20.transfer (
  chain      TEXT        NOT NULL,
  tx_hash    TEXT        NOT NULL,
  log_index  INTEGER     NOT NULL,
  token      TEXT        NOT NULL,   -- zETH | zUSDC | zBNB | zJPYC
  block_time TIMESTAMPTZ NOT NULL,
  day        DATE        NOT NULL,
  from_addr  TEXT        NOT NULL,
  to_addr    TEXT        NOT NULL,
  amount     NUMERIC     NOT NULL,   -- already scaled by the token's decimals
  PRIMARY KEY (chain, tx_hash, log_index)
);
CREATE INDEX transfer_day_idx ON zerc20.transfer (day);

-- Transactions carrying the zERC20 token's Teleport event. This is the ONLY
-- on-chain discriminator between a redeem (zAsset minted to a recipient after a
-- private transfer) and a wrap (zAsset minted against a deposit) — both are
-- mints from 0x0. Marker is per transaction, matching the Dune classification.
CREATE TABLE zerc20.teleport_tx (
  chain   TEXT NOT NULL,
  tx_hash TEXT NOT NULL,
  PRIMARY KEY (chain, tx_hash)
);

-- LiquidityManager Unwrapped events: the liquidity-curve fee actually charged.
-- Zero while a pool sits at or above target, which is most of the time.
CREATE TABLE zerc20.unwrap_fee (
  chain      TEXT        NOT NULL,
  tx_hash    TEXT        NOT NULL,
  log_index  INTEGER     NOT NULL,
  token      TEXT        NOT NULL,
  block_time TIMESTAMPTZ NOT NULL,
  day        DATE        NOT NULL,
  amount_out NUMERIC     NOT NULL,
  fee_amount NUMERIC     NOT NULL,
  PRIMARY KEY (chain, tx_hash, log_index)
);

-- Wallets kept out of the "organic" views. 'bot' rows come from the dev feed at
-- bot-addresses.zerc20.io (synced daily by the zerc20-bot-watch task, which used
-- to rewrite the Dune SQL and now writes here instead). 'system' rows are the
-- five internal wallets the protocol team confirmed on 2026-09-09 — the v1
-- relayer takes a second mint leg on most redeems, so leaving it in
-- double-counts a single user redeem.
CREATE TABLE zerc20.excluded_wallet (
  address    TEXT        NOT NULL,     -- lower-cased
  kind       TEXT        NOT NULL CHECK (kind IN ('bot', 'system')),
  label      TEXT,
  chain      TEXT,                     -- informational; exclusion is cross-chain
  source     TEXT,                     -- 'feed' | 'manual' | 'team'
  added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (address)
);

-- Fixed USD prices, matching the constants the Dune queries used, so every
-- figure stays comparable with what the dashboard and past reports show.
-- Changing a price is an UPDATE here, not a code change.
CREATE TABLE zerc20.token_price (
  token TEXT PRIMARY KEY,
  usd   NUMERIC NOT NULL,
  note  TEXT
);
INSERT INTO zerc20.token_price (token, usd, note) VALUES
  ('zETH',  1988.0,    'fixed constant, carried over from the Dune queries'),
  ('zUSDC', 1.0,       'USD peg'),
  ('zBNB',  600.0,     'fixed constant, carried over from the Dune queries'),
  ('zJPYC', 0.008908,  'fixed JPY rate, carried over from the Dune queries');

INSERT INTO zerc20.excluded_wallet (address, kind, label, source) VALUES
  ('0xac64fa8278a5cd9958e63985d73a99f1c65e51ce', 'system', 'v1 relayer module', 'team'),
  ('0x1fc708f502a68021f8a50452a57b0b2b7c5dc10f', 'system', 'system wallet', 'team'),
  ('0x71c7d41ab0cd6298eda2890305a1352c60c2af77', 'system', 'system wallet', 'team'),
  ('0x9d9e23ed4acb429ee4824c51f40b108e9fb19860', 'system', 'system wallet', 'team'),
  ('0xd88a41cdc87e4f773dbf90ca64df3f582d7b7b8a', 'system', 'system wallet', 'team');

-- One row per counted operation, classified the same way the Dune queries did:
-- a mint whose transaction carries a Teleport event is a Redeem, any other mint
-- is a Wrap, and a burn is an Unwrap. `wallet` is the user side of the leg.
CREATE VIEW zerc20.v_operation AS
SELECT
  t.chain,
  t.token,
  t.day,
  t.tx_hash,
  t.log_index,
  CASE
    WHEN t.from_addr = '0x0000000000000000000000000000000000000000'
         AND tp.tx_hash IS NOT NULL                                      THEN 'Redeem'
    WHEN t.from_addr = '0x0000000000000000000000000000000000000000'      THEN 'Wrap'
    ELSE                                                                      'Unwrap'
  END AS tx_type,
  CASE WHEN t.from_addr = '0x0000000000000000000000000000000000000000'
       THEN t.to_addr ELSE t.from_addr END AS wallet,
  t.amount,
  t.amount * p.usd AS usd_value,
  (x.address IS NULL) AS is_organic
FROM zerc20.transfer t
LEFT JOIN zerc20.teleport_tx tp
       ON tp.chain = t.chain AND tp.tx_hash = t.tx_hash
LEFT JOIN zerc20.token_price p ON p.token = t.token
LEFT JOIN zerc20.excluded_wallet x
       ON x.address = CASE WHEN t.from_addr = '0x0000000000000000000000000000000000000000'
                           THEN t.to_addr ELSE t.from_addr END
WHERE t.from_addr = '0x0000000000000000000000000000000000000000'
   OR t.to_addr   = '0x0000000000000000000000000000000000000000';

-- Dashboard shape: one row per day/chain/token/type, all wallets included.
-- Column names match the Dune result set so the front end needs no remapping.
CREATE VIEW zerc20.v_daily AS
SELECT day, chain, token, tx_type,
       COUNT(*)                      AS txs,
       ROUND(SUM(usd_value), 2)      AS volume_usd,
       COUNT(DISTINCT wallet)        AS unique_wallets
FROM zerc20.v_operation
GROUP BY day, chain, token, tx_type;

-- Same, with bot and system wallets removed: the "organic" view.
CREATE VIEW zerc20.v_daily_organic AS
SELECT day, chain, token, tx_type,
       COUNT(*)                      AS txs,
       ROUND(SUM(usd_value), 2)      AS volume_usd,
       COUNT(DISTINCT wallet)        AS unique_wallets
FROM zerc20.v_operation
WHERE is_organic
GROUP BY day, chain, token, tx_type;

-- migrate:down
DROP SCHEMA IF EXISTS zerc20 CASCADE;
