-- migrate:up

-- Personal finance / budget module ("Budget"). A ZenMoney-style DUAL-LEG ledger:
-- every transaction carries an optional outflow (account+asset+amount) and an
-- optional inflow (account+asset+amount). Expense = outflow only; income =
-- inflow only; transfer = both (handles ATM withdrawals, FX, crypto→fiat with
-- no double-counting). A unified "asset" abstraction (fiat, crypto, stock) lets
-- multi-currency accounts, multi-token wallets, and brokerage holdings share one
-- model. Mirrors ZenMoney's instrument/account/tag/merchant/transaction objects
-- so the import is a near-lossless field map.

-- --- assets: every unit of value (THB, USD, RUB, USDT, SOL, a ticker) --------
CREATE TABLE memory.fin_asset (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code              TEXT NOT NULL,                     -- 'THB', 'USDT', 'SOL'
  name              TEXT,
  kind              TEXT NOT NULL DEFAULT 'fiat',      -- fiat | crypto | stock
  decimals          SMALLINT NOT NULL DEFAULT 2,
  symbol            TEXT,                              -- '฿', '$'
  chain             TEXT,                              -- crypto: 'solana','tron','ethereum'
  contract_address  TEXT,                              -- crypto token contract
  price_feed_id     TEXT,                              -- e.g. coingecko id (Phase 2)
  zen_instrument_id INTEGER,                           -- ZenMoney instrument.id (import map)
  is_active         BOOLEAN NOT NULL DEFAULT true,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fin_asset_kind_check CHECK (kind IN ('fiat','crypto','stock'))
);
-- one row per code+chain (USDT exists on several chains; fiat has null chain)
CREATE UNIQUE INDEX fin_asset_code_chain_idx
  ON memory.fin_asset (code, COALESCE(chain, ''));
CREATE UNIQUE INDEX fin_asset_zen_idx
  ON memory.fin_asset (zen_instrument_id) WHERE zen_instrument_id IS NOT NULL;
CREATE TRIGGER fin_asset_touch BEFORE UPDATE ON memory.fin_asset
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

-- --- accounts: a container of value -----------------------------------------
CREATE TABLE memory.fin_account (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name                  TEXT NOT NULL,
  kind                  TEXT NOT NULL DEFAULT 'bank',  -- bank|cash|crypto_wallet|cex|dex|brokerage|debt
  currency_asset_id     UUID REFERENCES memory.fin_asset(id) ON DELETE SET NULL,
  owner                 TEXT NOT NULL DEFAULT 'me',    -- me|wife|son (future sharing)
  account_group         TEXT,                          -- dashboard grouping ('Banks','Crypto','Wife')
  institution           TEXT,
  wallet_address        TEXT,                          -- crypto (Phase-2 auto-sync)
  chain                 TEXT,
  person_id             UUID REFERENCES canonical.person(id) ON DELETE SET NULL,  -- debt counterparty
  opening_balance       NUMERIC NOT NULL DEFAULT 0,
  include_in_net_worth  BOOLEAN NOT NULL DEFAULT true,  -- ZenMoney inBalance
  archived              BOOLEAN NOT NULL DEFAULT false,
  sort                  INTEGER NOT NULL DEFAULT 0,
  zen_user_id           TEXT,                           -- ZenMoney account.user
  source_kind           TEXT,                           -- manual | zenmoney
  source_ref            TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at            TIMESTAMPTZ,
  CONSTRAINT fin_account_kind_check
    CHECK (kind IN ('bank','cash','crypto_wallet','cex','dex','brokerage','debt')),
  CONSTRAINT fin_account_owner_check CHECK (owner IN ('me','wife','son'))
);
CREATE UNIQUE INDEX fin_account_source_idx
  ON memory.fin_account (source_kind, source_ref) WHERE source_ref IS NOT NULL;
CREATE INDEX fin_account_live_idx ON memory.fin_account (archived) WHERE deleted_at IS NULL;
CREATE TRIGGER fin_account_touch BEFORE UPDATE ON memory.fin_account
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

-- --- categories: a tree (opp_stage-style text-key lookup) --------------------
CREATE TABLE memory.fin_category (
  key         TEXT PRIMARY KEY,                  -- slug, or ZenMoney tag uuid
  label       TEXT NOT NULL,
  parent_key  TEXT REFERENCES memory.fin_category(key) ON DELETE SET NULL,
  kind        TEXT NOT NULL DEFAULT 'expense',   -- expense | income | both
  sort        INTEGER NOT NULL DEFAULT 0,
  color       TEXT NOT NULL DEFAULT 'slate',
  icon        TEXT,
  source_kind TEXT,
  source_ref  TEXT,                              -- ZenMoney tag id
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fin_category_kind_check CHECK (kind IN ('expense','income','both'))
);
CREATE TRIGGER fin_category_touch BEFORE UPDATE ON memory.fin_category
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

-- --- payees (light; a payee may also be free text on the transaction) --------
CREATE TABLE memory.fin_payee (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  person_id   UUID REFERENCES canonical.person(id) ON DELETE SET NULL,
  source_kind TEXT,
  source_ref  TEXT,                              -- ZenMoney merchant id
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX fin_payee_source_idx
  ON memory.fin_payee (source_kind, source_ref) WHERE source_ref IS NOT NULL;
CREATE TRIGGER fin_payee_touch BEFORE UPDATE ON memory.fin_payee
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

-- --- import batches (review-before-commit, like bot_capture) -----------------
CREATE TABLE memory.fin_import_batch (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind        TEXT NOT NULL,                     -- pdf | csv | zenmoney
  filename    TEXT,
  account_id  UUID REFERENCES memory.fin_account(id) ON DELETE SET NULL,
  status      TEXT NOT NULL DEFAULT 'pending',   -- pending | confirmed | discarded
  parsed      JSONB,                             -- LLM/CSV-extracted rows awaiting review
  row_count   INTEGER NOT NULL DEFAULT 0,
  note        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at  TIMESTAMPTZ,
  CONSTRAINT fin_import_batch_status_check CHECK (status IN ('pending','confirmed','discarded'))
);

-- --- transactions: the dual-leg ledger --------------------------------------
CREATE TABLE memory.fin_transaction (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  txn_date            DATE NOT NULL,
  outflow_account_id  UUID REFERENCES memory.fin_account(id) ON DELETE SET NULL,
  outflow_asset_id    UUID REFERENCES memory.fin_asset(id) ON DELETE SET NULL,
  outflow_amount      NUMERIC,                   -- in outflow asset units
  inflow_account_id   UUID REFERENCES memory.fin_account(id) ON DELETE SET NULL,
  inflow_asset_id     UUID REFERENCES memory.fin_asset(id) ON DELETE SET NULL,
  inflow_amount       NUMERIC,                   -- in inflow asset units
  category_key        TEXT REFERENCES memory.fin_category(key) ON DELETE SET NULL,
  payee_id            UUID REFERENCES memory.fin_payee(id) ON DELETE SET NULL,
  payee_text          TEXT,
  person_id           UUID REFERENCES canonical.person(id) ON DELETE SET NULL,
  note                TEXT,
  tags                TEXT[] NOT NULL DEFAULT '{}',
  import_batch_id     UUID REFERENCES memory.fin_import_batch(id) ON DELETE SET NULL,
  source_kind         TEXT,                      -- manual | zenmoney | pdf_import | csv_import
  source_ref          TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at          TIMESTAMPTZ,
  -- at least one leg must be present
  CONSTRAINT fin_txn_has_leg CHECK (outflow_account_id IS NOT NULL OR inflow_account_id IS NOT NULL)
);
CREATE UNIQUE INDEX fin_transaction_source_idx
  ON memory.fin_transaction (source_kind, source_ref) WHERE source_ref IS NOT NULL;
CREATE INDEX fin_transaction_date_idx ON memory.fin_transaction (txn_date DESC) WHERE deleted_at IS NULL;
CREATE INDEX fin_transaction_outflow_idx ON memory.fin_transaction (outflow_account_id) WHERE deleted_at IS NULL;
CREATE INDEX fin_transaction_inflow_idx ON memory.fin_transaction (inflow_account_id) WHERE deleted_at IS NULL;
CREATE INDEX fin_transaction_category_idx ON memory.fin_transaction (category_key) WHERE deleted_at IS NULL;
CREATE TRIGGER fin_transaction_touch BEFORE UPDATE ON memory.fin_transaction
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

-- --- FX / price rates: asset → USD numeraire by date ------------------------
CREATE TABLE memory.fin_fx_rate (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id    UUID NOT NULL REFERENCES memory.fin_asset(id) ON DELETE CASCADE,
  quote       TEXT NOT NULL DEFAULT 'USD',       -- price expressed in this currency
  rate        NUMERIC NOT NULL,                  -- 1 unit of asset = `rate` quote
  rate_date   DATE NOT NULL,
  source      TEXT,                              -- 'frankfurter' | 'manual' | 'zenmoney' | 'coingecko'
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX fin_fx_rate_uniq_idx ON memory.fin_fx_rate (asset_id, quote, rate_date);

-- --- per-(account,asset) balance: opening + Σinflow − Σoutflow ---------------
-- Opening balance is modeled as a synthetic leg in the account's currency, so
-- the running balance is just SUM(delta) per (account, asset).
CREATE VIEW memory.fin_account_balance AS
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

-- --- seed: starter fiat assets (import + frankfurter fill the rest) ----------
INSERT INTO memory.fin_asset (code, name, kind, decimals, symbol) VALUES
  ('USD', 'US Dollar',        'fiat', 2, '$'),
  ('THB', 'Thai Baht',        'fiat', 2, '฿'),
  ('RUB', 'Russian Ruble',    'fiat', 2, '₽'),
  ('EUR', 'Euro',             'fiat', 2, '€'),
  ('USDT','Tether',           'crypto', 6, '$'),
  ('USDC','USD Coin',         'crypto', 6, '$'),
  ('BTC', 'Bitcoin',          'crypto', 8, '₿'),
  ('SOL', 'Solana',           'crypto', 9, NULL)
ON CONFLICT DO NOTHING;

-- USD numeraire + stablecoin parity so net worth works before the FX worker runs
INSERT INTO memory.fin_fx_rate (asset_id, quote, rate, rate_date, source)
SELECT id, 'USD', 1, CURRENT_DATE, 'seed'
  FROM memory.fin_asset WHERE code IN ('USD','USDT','USDC')
ON CONFLICT DO NOTHING;

-- a catch-all category so uncategorised imports have a home
INSERT INTO memory.fin_category (key, label, kind, sort, color) VALUES
  ('uncategorized', 'Uncategorized', 'both', 999, 'slate')
ON CONFLICT DO NOTHING;

-- migrate:down

DROP VIEW IF EXISTS memory.fin_account_balance;
DROP TABLE IF EXISTS memory.fin_fx_rate;
DROP TABLE IF EXISTS memory.fin_transaction;
DROP TABLE IF EXISTS memory.fin_import_batch;
DROP TABLE IF EXISTS memory.fin_payee;
DROP TABLE IF EXISTS memory.fin_category;
DROP TABLE IF EXISTS memory.fin_account;
DROP TABLE IF EXISTS memory.fin_asset;
