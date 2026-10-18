-- migrate:up

-- Accounts split into two classes: OPERATIONAL (banks, cash, crypto wallets you
-- transact with) vs INVESTMENT (brokerages, long-hold crypto — the "Portfolio").
-- Drives the Overview net-worth breakdown and which tab an account appears on.
ALTER TABLE memory.fin_account
  ADD COLUMN IF NOT EXISTS account_class TEXT NOT NULL DEFAULT 'operational';
ALTER TABLE memory.fin_account
  ADD CONSTRAINT fin_account_class_check CHECK (account_class IN ('operational','investment'));

-- sensible backfill: brokerages are investments, everything else operational
UPDATE memory.fin_account SET account_class = 'investment' WHERE kind = 'brokerage';

-- Editable cutoff: only sync crypto transfers dated on/after this (the owner may
-- change it). Stored in the existing app_setting key/value table.
INSERT INTO memory.app_setting (key, value)
VALUES ('crypto_tx_cutoff', '"2026-06-01"'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- migrate:down

DELETE FROM memory.app_setting WHERE key = 'crypto_tx_cutoff';
ALTER TABLE memory.fin_account DROP CONSTRAINT IF EXISTS fin_account_class_check;
ALTER TABLE memory.fin_account DROP COLUMN IF EXISTS account_class;
