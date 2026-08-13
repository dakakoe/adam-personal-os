-- migrate:up

-- Holdings = a snapshot of how much of an asset an account holds. Used for
-- crypto wallets (chain-synced) and brokerage positions (manual). An account
-- that has ANY holding rows is "snapshot-tracked" — its balance comes from
-- holdings, not the transaction ledger; accounts with none stay ledger-derived
-- (banks, cash, debts, the USD crypto-cash tracking account).
CREATE TABLE memory.fin_holding (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id  UUID NOT NULL REFERENCES memory.fin_account(id) ON DELETE CASCADE,
  asset_id    UUID NOT NULL REFERENCES memory.fin_asset(id) ON DELETE CASCADE,
  quantity    NUMERIC NOT NULL DEFAULT 0,
  cost_basis_usd NUMERIC,                       -- optional avg cost for P&L (manual)
  source      TEXT NOT NULL DEFAULT 'manual',   -- manual | chain
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fin_holding_uniq UNIQUE (account_id, asset_id)
);
CREATE TRIGGER fin_holding_touch BEFORE UPDATE ON memory.fin_holding
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

-- Balance = holdings snapshot for accounts that have holdings; otherwise the
-- transaction-ledger sum (opening + Σ legs).
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
   AND l.account_id NOT IN (SELECT account_id FROM held)   -- ledger-tracked only
 GROUP BY l.account_id, l.asset_id
UNION ALL
SELECT h.account_id, h.asset_id, h.quantity AS balance
  FROM memory.fin_holding h
 WHERE h.quantity <> 0;

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
)
SELECT account_id, asset_id, SUM(delta) AS balance
  FROM legs
 WHERE asset_id IS NOT NULL
 GROUP BY account_id, asset_id;

DROP TABLE IF EXISTS memory.fin_holding;
