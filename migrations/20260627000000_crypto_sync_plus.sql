-- migrate:up

-- crypto-sync++ (Budget Phase 2):
--   1. per-wallet sync health        → memory.fin_wallet_sync
--   2. gas as ledger legs            → uses the 'network-fees' category below
--   3. per-tx USD value at block time → memory.fin_transaction.usd_value
--
-- Sync health lives in its OWN table (not columns on fin_account) on purpose:
-- fin_account carries a fin_audit AFTER-UPDATE trigger, so stamping health on
-- every daily wallet sync would flood the audit log with system rows. This
-- table is deliberately un-audited — it is operational telemetry, not a
-- user-editable finance record.
CREATE TABLE IF NOT EXISTS memory.fin_wallet_sync (
  account_id      UUID PRIMARY KEY REFERENCES memory.fin_account(id) ON DELETE CASCADE,
  last_synced_at  TIMESTAMPTZ,
  status          TEXT,                 -- ok | partial | error
  error           TEXT,
  summary         JSONB NOT NULL DEFAULT '{}',  -- {chains, chains_failed, assets, transfers, gas, ...}
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fin_wallet_sync_status_check
    CHECK (status IS NULL OR status IN ('ok','partial','error'))
);

-- Per-transaction USD value captured at the transfer's block time (not "now").
-- Stablecoins ≈ qty; native coins priced via the historical rate cached in
-- fin_fx_rate keyed by the block date. NULL = not (yet) priced.
ALTER TABLE memory.fin_transaction ADD COLUMN IF NOT EXISTS usd_value NUMERIC;

-- Gas / network fees land here as single-leg outflow transactions.
INSERT INTO memory.fin_category (key, label, kind, color, icon, sort, source_kind)
VALUES ('network-fees', 'Network Fees', 'expense', 'amber', 'fuel', 900, 'system')
ON CONFLICT (key) DO NOTHING;

-- migrate:down

ALTER TABLE memory.fin_transaction DROP COLUMN IF EXISTS usd_value;
DROP TABLE IF EXISTS memory.fin_wallet_sync;
DELETE FROM memory.fin_category WHERE key = 'network-fees' AND source_kind = 'system';
