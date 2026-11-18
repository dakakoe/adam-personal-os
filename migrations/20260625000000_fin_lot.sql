-- migrate:up

-- FIFO cost-lot investment tracking. Two immutable-fact tables that record the
-- raw buy/sell events for a position; the cost-lot engine reads ALL lots + sales
-- for an (account, asset) and computes FIFO matching, realised P&L, and each
-- lot's REMAINING quantity in code. Deliberately no remaining_quantity column —
-- remaining qty is derived (lot.quantity minus FIFO-consumed sells), never stored,
-- so the facts stay append-only and can't drift from the computed truth.

-- --- buy lots: one row per purchase (immutable fact) -------------------------
CREATE TABLE memory.fin_lot (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id         UUID NOT NULL REFERENCES memory.fin_account(id) ON DELETE CASCADE,
  asset_id           UUID NOT NULL REFERENCES memory.fin_asset(id) ON DELETE CASCADE,
  open_date          DATE NOT NULL,
  quantity           NUMERIC NOT NULL,            -- units bought
  cost_per_unit_usd  NUMERIC NOT NULL,            -- USD paid per unit (cost basis)
  note               TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX fin_lot_account_asset_idx ON memory.fin_lot (account_id, asset_id);
CREATE TRIGGER fin_lot_touch BEFORE UPDATE ON memory.fin_lot
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();
CREATE TRIGGER fin_lot_audit AFTER INSERT OR UPDATE OR DELETE ON memory.fin_lot
  FOR EACH ROW EXECUTE FUNCTION memory._fin_audit();

-- --- sells: one row per disposal (immutable fact; FIFO matched in code) ------
CREATE TABLE memory.fin_lot_sale (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id             UUID NOT NULL REFERENCES memory.fin_account(id) ON DELETE CASCADE,
  asset_id               UUID NOT NULL REFERENCES memory.fin_asset(id) ON DELETE CASCADE,
  sale_date              DATE NOT NULL,
  quantity               NUMERIC NOT NULL,        -- units sold
  proceeds_per_unit_usd  NUMERIC NOT NULL,        -- USD received per unit
  note                   TEXT,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX fin_lot_sale_account_asset_idx ON memory.fin_lot_sale (account_id, asset_id);
CREATE TRIGGER fin_lot_sale_touch BEFORE UPDATE ON memory.fin_lot_sale
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();
CREATE TRIGGER fin_lot_sale_audit AFTER INSERT OR UPDATE OR DELETE ON memory.fin_lot_sale
  FOR EACH ROW EXECUTE FUNCTION memory._fin_audit();

-- migrate:down

DROP TRIGGER IF EXISTS fin_lot_sale_audit ON memory.fin_lot_sale;
DROP TRIGGER IF EXISTS fin_lot_sale_touch ON memory.fin_lot_sale;
DROP TABLE IF EXISTS memory.fin_lot_sale;
DROP TRIGGER IF EXISTS fin_lot_audit ON memory.fin_lot;
DROP TRIGGER IF EXISTS fin_lot_touch ON memory.fin_lot;
DROP TABLE IF EXISTS memory.fin_lot;
