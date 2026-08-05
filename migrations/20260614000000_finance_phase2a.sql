-- migrate:up

-- Payees can optionally link to a company in the CRM (memory.company) — not
-- required; a payee may stay a bare name.
ALTER TABLE memory.fin_payee
  ADD COLUMN company_id UUID REFERENCES memory.company(id) ON DELETE SET NULL;

-- Budget targets: a spending limit per category per period (monthly in v1),
-- stored in the USD numeraire so it survives the THB/USD toggle.
CREATE TABLE memory.fin_budget (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  category_key TEXT NOT NULL REFERENCES memory.fin_category(key) ON DELETE CASCADE,
  period       TEXT NOT NULL DEFAULT 'monthly',   -- monthly (room for weekly/yearly later)
  limit_usd    NUMERIC NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fin_budget_period_check CHECK (period IN ('monthly','weekly','yearly'))
);
CREATE UNIQUE INDEX fin_budget_cat_period_idx ON memory.fin_budget (category_key, period);
CREATE TRIGGER fin_budget_touch BEFORE UPDATE ON memory.fin_budget
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

-- migrate:down

DROP TABLE IF EXISTS memory.fin_budget;
ALTER TABLE memory.fin_payee DROP COLUMN IF EXISTS company_id;
