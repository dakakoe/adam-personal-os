-- migrate:up

-- zERC20 v2. v2 replaced the v1 design in October 2026: deposits wrap into
-- per-chain zTokens, balances become notes in a Merkle tree, and a relayer
-- submits sends and withdrawals through one Verifier contract. v1 still runs
-- (a few dozen operations a week) and its tables are untouched; v2 gets its
-- own, because the two protocols expose different facts.
--
-- What v2 makes public, and what it does not:
--   * deposits (zToken Wrapped) — amount public;
--   * withdrawals (Verifier Transacted with withdrawAmount > 0) — gross amount
--     and the exact protocol fee are emitted, so fees are read, never modelled;
--   * private SENDS (Transacted with withdrawAmount = 0) — only the fact of the
--     send is public; the amount is hidden by design, so it is counted, never
--     valued. No v2 "volume" figure may silently include it.

-- Token registry, per chain: token ids are assigned per chain (zJPYC is id 3
-- on Polygon, zUSDC id 2), so a bare token id means nothing without its chain.
-- Refreshed from ZERC20Factory.tokenById() on every indexer run, which is also
-- how a newly listed token gets picked up without a code change.
CREATE TABLE zerc20.v2_token (
  chain       TEXT        NOT NULL,
  token_id    INTEGER     NOT NULL,
  ztoken      TEXT        NOT NULL,   -- zToken contract, lower-cased
  underlying  TEXT        NOT NULL,   -- 0xeeee… for the native coin
  symbol      TEXT        NOT NULL,   -- zETH | zUSDC | zBNB | zPOL | zJPYC
  decimals    INTEGER     NOT NULL,
  seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chain, token_id),
  UNIQUE (chain, ztoken)
);

-- Deposits: zToken Wrapped(caller, receiver, amount). Both the direct wrap and
-- a deposit-address flush end here. `receiver` is a burn address, unique per
-- deposit by design, so depositor counts use `caller` instead.
CREATE TABLE zerc20.v2_wrap (
  chain      TEXT        NOT NULL,
  tx_hash    TEXT        NOT NULL,
  log_index  INTEGER     NOT NULL,
  ztoken     TEXT        NOT NULL,
  block_time TIMESTAMPTZ NOT NULL,
  day        DATE        NOT NULL,
  caller     TEXT        NOT NULL,
  receiver   TEXT        NOT NULL,
  amount     NUMERIC     NOT NULL,   -- scaled by the token's decimals
  PRIMARY KEY (chain, tx_hash, log_index)
);
CREATE INDEX v2_wrap_day_idx ON zerc20.v2_wrap (day);

-- Exits to the underlying asset: zToken Unwrapped(caller, receiver, amount).
-- NOT the withdrawal total. Measured 2026-11-03 across every v2 withdrawal: a
-- withdrawal pays out either as the underlying (one Unwrapped, for the amount
-- net of fee) or as public zTokens minted to the recipient (no Unwrapped at
-- all) — zUSDC and zJPYC withdrawals are mostly the second kind. Transacted is
-- the one event every withdrawal has, so it, not this, defines money out. This
-- table is kept as the record of exits to the underlying, including direct
-- unwraps from a connected wallet.
CREATE TABLE zerc20.v2_unwrap (
  chain      TEXT        NOT NULL,
  tx_hash    TEXT        NOT NULL,
  log_index  INTEGER     NOT NULL,
  ztoken     TEXT        NOT NULL,
  block_time TIMESTAMPTZ NOT NULL,
  day        DATE        NOT NULL,
  caller     TEXT        NOT NULL,
  receiver   TEXT        NOT NULL,
  amount     NUMERIC     NOT NULL,
  PRIMARY KEY (chain, tx_hash, log_index)
);
CREATE INDEX v2_unwrap_day_idx ON zerc20.v2_unwrap (day);

-- zToken transfers between two wallets (neither side 0x0). A payment to a
-- burn address is one of these, with its amount public and only the recipient
-- unlinkable — but a burn address is indistinguishable from an ordinary wallet
-- by design, so these are "transfers that may include private payments", never
-- labelled as private sends. Deposits also move zTokens wallet-to-burn-address
-- inside the same transaction; the view excludes those so a deposit is not
-- counted twice.
CREATE TABLE zerc20.v2_transfer (
  chain      TEXT        NOT NULL,
  tx_hash    TEXT        NOT NULL,
  log_index  INTEGER     NOT NULL,
  ztoken     TEXT        NOT NULL,
  block_time TIMESTAMPTZ NOT NULL,
  day        DATE        NOT NULL,
  from_addr  TEXT        NOT NULL,
  to_addr    TEXT        NOT NULL,
  amount     NUMERIC     NOT NULL,
  PRIMARY KEY (chain, tx_hash, log_index)
);
CREATE INDEX v2_transfer_tx_idx ON zerc20.v2_transfer (chain, tx_hash);

-- Verifier Transacted: one row per private send or withdrawal.
CREATE TABLE zerc20.v2_transact (
  chain              TEXT        NOT NULL,
  tx_hash            TEXT        NOT NULL,
  log_index          INTEGER     NOT NULL,
  token_id           INTEGER     NOT NULL,
  block_time         TIMESTAMPTZ NOT NULL,
  day                DATE        NOT NULL,
  caller             TEXT        NOT NULL,   -- usually the relayer
  kind               TEXT        NOT NULL CHECK (kind IN ('send', 'withdraw')),
  withdraw_recipient TEXT,                   -- NULL for a send
  withdraw_amount    NUMERIC     NOT NULL,   -- 0 for a send: hidden, not zero-valued
  fee_amount         NUMERIC     NOT NULL,   -- exact protocol fee, in token units
  PRIMARY KEY (chain, tx_hash, log_index)
);
CREATE INDEX v2_transact_day_idx ON zerc20.v2_transact (day);

-- Verifier Merged: up to 100 notes folded into one. Counted, never valued.
CREATE TABLE zerc20.v2_merge (
  chain       TEXT        NOT NULL,
  tx_hash     TEXT        NOT NULL,
  log_index   INTEGER     NOT NULL,
  token_id    INTEGER     NOT NULL,
  block_time  TIMESTAMPTZ NOT NULL,
  day         DATE        NOT NULL,
  input_count INTEGER     NOT NULL,
  PRIMARY KEY (chain, tx_hash, log_index)
);

-- Verifier FeeAccrued(tokenId, recipient, amount). Stored separately from
-- Transacted.feeAmount so the two can be reconciled before either is reported.
CREATE TABLE zerc20.v2_fee_accrued (
  chain      TEXT        NOT NULL,
  tx_hash    TEXT        NOT NULL,
  log_index  INTEGER     NOT NULL,
  token_id   INTEGER     NOT NULL,
  block_time TIMESTAMPTZ NOT NULL,
  day        DATE        NOT NULL,
  recipient  TEXT        NOT NULL,
  amount     NUMERIC     NOT NULL,
  PRIMARY KEY (chain, tx_hash, log_index)
);

-- Verifier RageExited: the contracts-only escape hatch for a stuck deposit.
-- Rare by design; worth seeing when it happens.
CREATE TABLE zerc20.v2_rage_exit (
  chain      TEXT        NOT NULL,
  tx_hash    TEXT        NOT NULL,
  log_index  INTEGER     NOT NULL,
  token_id   INTEGER     NOT NULL,
  block_time TIMESTAMPTZ NOT NULL,
  day        DATE        NOT NULL,
  recipient  TEXT        NOT NULL,
  amount     NUMERIC     NOT NULL,
  PRIMARY KEY (chain, tx_hash, log_index)
);

-- Backing held per contract, sampled on every indexer run, for v1 and v2.
-- totalSupply is NOT TVL in either version: private transfers strand tokens in
-- burn addresses, so supply runs well above the assets actually held (2-3x on
-- v2, ~10x on v1). This table records what is held, which is what TVL means.
CREATE TABLE zerc20.tvl_snapshot (
  taken_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  version    TEXT        NOT NULL CHECK (version IN ('v1', 'v2')),
  chain      TEXT        NOT NULL,
  token      TEXT        NOT NULL,   -- zToken symbol
  holder     TEXT        NOT NULL,   -- v1 LiquidityManager or v2 zToken
  amount     NUMERIC     NOT NULL,   -- underlying held, scaled
  PRIMARY KEY (taken_at, version, chain, holder)
);
CREATE INDEX tvl_snapshot_latest_idx ON zerc20.tvl_snapshot (version, chain, token, taken_at DESC);

-- zPOL is new in v2. Fixed like the others, so v1 and v2 figures stay on one
-- price basis; set from the market price on 2026-11-03.
INSERT INTO zerc20.token_price (token, usd, note) VALUES
  ('zPOL', 0.0874, 'fixed constant, POL market price on 2026-11-03')
ON CONFLICT (token) DO NOTHING;

-- One row per v2 operation, valued where v2 makes the value public.
-- tx_type: Deposit (wrap into the pool), Withdraw (out of the private pool,
-- in either payout form), Transfer (zTokens between wallets, outside deposits
-- and withdrawals — may include private payments to burn addresses), Send
-- (note-to-note, amount hidden), Merge (note folding). usd_value is NULL for Send and Merge
-- on purpose: those amounts are not on chain.
CREATE VIEW zerc20.v2_operation AS
SELECT w.chain, t.symbol AS token, w.day, w.block_time, w.tx_hash, w.log_index,
       'Deposit'::text AS tx_type, w.caller AS wallet,
       w.amount, w.amount * p.usd AS usd_value, 0::numeric AS fee_usd,
       (x.address IS NULL) AS is_organic
FROM zerc20.v2_wrap w
JOIN zerc20.v2_token t ON t.chain = w.chain AND t.ztoken = w.ztoken
LEFT JOIN zerc20.token_price p ON p.token = t.symbol
LEFT JOIN zerc20.excluded_wallet x ON x.address = w.caller
UNION ALL
SELECT w.chain, t.symbol, w.day, w.block_time, w.tx_hash, w.log_index,
       'Withdraw', w.withdraw_recipient,
       -- gross: what left the private balance. The fee is part of it, and
       -- recipient received withdraw_amount - fee_amount.
       w.withdraw_amount, w.withdraw_amount * p.usd, w.fee_amount * p.usd,
       (x.address IS NULL)
FROM zerc20.v2_transact w
JOIN zerc20.v2_token t ON t.chain = w.chain AND t.token_id = w.token_id
LEFT JOIN zerc20.token_price p ON p.token = t.symbol
LEFT JOIN zerc20.excluded_wallet x ON x.address = w.withdraw_recipient
WHERE w.kind = 'withdraw'
UNION ALL
SELECT x.chain, t.symbol, x.day, x.block_time, x.tx_hash, x.log_index,
       'Transfer', x.from_addr,
       x.amount, x.amount * p.usd, 0::numeric,
       (e.address IS NULL)
FROM zerc20.v2_transfer x
JOIN zerc20.v2_token t ON t.chain = x.chain AND t.ztoken = x.ztoken
LEFT JOIN zerc20.token_price p ON p.token = t.symbol
LEFT JOIN zerc20.excluded_wallet e ON e.address = x.from_addr
-- deposit and withdrawal mechanics move zTokens too; those are already
-- counted as the deposit or withdrawal itself
WHERE NOT EXISTS (SELECT 1 FROM zerc20.v2_wrap w WHERE w.chain = x.chain AND w.tx_hash = x.tx_hash)
  AND NOT EXISTS (SELECT 1 FROM zerc20.v2_transact a WHERE a.chain = x.chain AND a.tx_hash = x.tx_hash)
UNION ALL
SELECT s.chain, t.symbol, s.day, s.block_time, s.tx_hash, s.log_index,
       'Send', s.caller, NULL, NULL, 0, TRUE
FROM zerc20.v2_transact s
JOIN zerc20.v2_token t ON t.chain = s.chain AND t.token_id = s.token_id
WHERE s.kind = 'send'
UNION ALL
SELECT m.chain, t.symbol, m.day, m.block_time, m.tx_hash, m.log_index,
       'Merge', NULL, NULL, NULL, 0, TRUE
FROM zerc20.v2_merge m
JOIN zerc20.v2_token t ON t.chain = m.chain AND t.token_id = m.token_id;

-- Dashboard shape, close to v1's so the front end reuses its formatting:
-- volume_usd is NULL for Send and Merge rows rather than a misleading 0.
CREATE VIEW zerc20.v2_daily AS
SELECT day, chain, token, tx_type,
       count(*)                                   AS txs,
       round(sum(usd_value), 2)                   AS volume_usd,
       count(DISTINCT wallet)                     AS unique_wallets,
       round(sum(fee_usd), 2)                     AS fees_usd
FROM zerc20.v2_operation
GROUP BY day, chain, token, tx_type;

CREATE VIEW zerc20.v2_daily_organic AS
SELECT day, chain, token, tx_type,
       count(*)                                   AS txs,
       round(sum(usd_value), 2)                   AS volume_usd,
       count(DISTINCT wallet)                     AS unique_wallets,
       round(sum(fee_usd), 2)                     AS fees_usd
FROM zerc20.v2_operation
WHERE is_organic
GROUP BY day, chain, token, tx_type;

-- Latest backing per holder, valued: what a TVL figure should read.
CREATE VIEW zerc20.tvl_latest AS
SELECT DISTINCT ON (s.version, s.chain, s.holder)
       s.version, s.chain, s.token, s.holder, s.amount,
       s.amount * p.usd AS usd, s.taken_at
FROM zerc20.tvl_snapshot s
LEFT JOIN zerc20.token_price p ON p.token = s.token
ORDER BY s.version, s.chain, s.holder, s.taken_at DESC;

-- migrate:down
DROP VIEW IF EXISTS zerc20.tvl_latest;
DROP VIEW IF EXISTS zerc20.v2_daily_organic;
DROP VIEW IF EXISTS zerc20.v2_daily;
DROP VIEW IF EXISTS zerc20.v2_operation;
DROP TABLE IF EXISTS zerc20.tvl_snapshot, zerc20.v2_rage_exit, zerc20.v2_fee_accrued,
                     zerc20.v2_transfer,
                     zerc20.v2_merge, zerc20.v2_transact, zerc20.v2_unwrap,
                     zerc20.v2_wrap, zerc20.v2_token;
DELETE FROM zerc20.token_price WHERE token = 'zPOL';
