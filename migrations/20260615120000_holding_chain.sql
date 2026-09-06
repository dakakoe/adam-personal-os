-- migrate:up

-- A holding now tracks WHICH chain it sits on, so one wallet account can hold
-- the same asset on several chains (USDT on BSC + USDT on Arbitrum + ETH on
-- Ethereum + ETH on Optimism …), Metamask-style. NULL chain = a manual holding
-- (e.g. a brokerage position) with no chain.
ALTER TABLE memory.fin_holding ADD COLUMN IF NOT EXISTS chain text;
ALTER TABLE memory.fin_holding DROP CONSTRAINT IF EXISTS fin_holding_uniq;
CREATE UNIQUE INDEX IF NOT EXISTS fin_holding_uniq
  ON memory.fin_holding (account_id, asset_id, COALESCE(chain, ''));

-- Balance view: sum each asset across its chains so net worth still aggregates
-- per (account, asset) (USDT total across BSC+ARB, ETH total across L2s, …).
CREATE OR REPLACE VIEW memory.fin_account_balance AS
WITH legs AS (
  SELECT a.id AS account_id, a.currency_asset_id AS asset_id, a.opening_balance AS delta
    FROM memory.fin_account a
   WHERE a.deleted_at IS NULL AND a.currency_asset_id IS NOT NULL AND a.opening_balance <> 0
  UNION ALL
  SELECT outflow_account_id, outflow_asset_id, -COALESCE(outflow_amount, 0)
    FROM memory.fin_transaction
   WHERE deleted_at IS NULL AND outflow_account_id IS NOT NULL
  UNION ALL
  SELECT inflow_account_id, inflow_asset_id, COALESCE(inflow_amount, 0)
    FROM memory.fin_transaction
   WHERE deleted_at IS NULL AND inflow_account_id IS NOT NULL
),
held AS (
  SELECT DISTINCT account_id FROM memory.fin_holding
)
SELECT l.account_id, l.asset_id, SUM(l.delta) AS balance
  FROM legs l
 WHERE l.asset_id IS NOT NULL
   AND l.account_id NOT IN (SELECT account_id FROM held)
 GROUP BY l.account_id, l.asset_id
UNION ALL
SELECT h.account_id, h.asset_id, SUM(h.quantity) AS balance
  FROM memory.fin_holding h
 GROUP BY h.account_id, h.asset_id
HAVING SUM(h.quantity) <> 0;

-- migrate:down

CREATE OR REPLACE VIEW memory.fin_account_balance AS
WITH legs AS (
  SELECT a.id AS account_id, a.currency_asset_id AS asset_id, a.opening_balance AS delta
    FROM memory.fin_account a
   WHERE a.deleted_at IS NULL AND a.currency_asset_id IS NOT NULL AND a.opening_balance <> 0
  UNION ALL
  SELECT outflow_account_id, outflow_asset_id, -COALESCE(outflow_amount, 0)
    FROM memory.fin_transaction
   WHERE deleted_at IS NULL AND outflow_account_id IS NOT NULL
  UNION ALL
  SELECT inflow_account_id, inflow_asset_id, COALESCE(inflow_amount, 0)
    FROM memory.fin_transaction
   WHERE deleted_at IS NULL AND inflow_account_id IS NOT NULL
),
held AS (SELECT DISTINCT account_id FROM memory.fin_holding)
SELECT l.account_id, l.asset_id, SUM(l.delta) AS balance
  FROM legs l
 WHERE l.asset_id IS NOT NULL AND l.account_id NOT IN (SELECT account_id FROM held)
 GROUP BY l.account_id, l.asset_id
UNION ALL
SELECT h.account_id, h.asset_id, h.quantity AS balance
  FROM memory.fin_holding h WHERE h.quantity <> 0;

DROP INDEX IF EXISTS memory.fin_holding_uniq;
ALTER TABLE memory.fin_holding ADD CONSTRAINT fin_holding_uniq UNIQUE (account_id, asset_id);
ALTER TABLE memory.fin_holding DROP COLUMN IF EXISTS chain;
