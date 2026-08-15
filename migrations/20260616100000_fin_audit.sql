-- migrate:up

-- Append-only audit of every change to the user-editable budget tables, so a bad
-- edit (e.g. by the budget-only web user) can be recovered by replaying old_row.
-- actor comes from a per-request GUC (app.actor) the API sets to 'owner'/'member'/
-- 'system'; before/after rows are full jsonb snapshots. No undo UI — recovery is
-- done by inspecting this table and applying the inverse change.

CREATE TABLE memory.fin_audit (
  id          BIGSERIAL PRIMARY KEY,
  at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor       TEXT,                 -- who: 'owner' | 'member' | 'system' (best-effort)
  table_name  TEXT NOT NULL,
  op          TEXT NOT NULL,        -- INSERT | UPDATE | DELETE
  row_pk      TEXT,                 -- the row's id/key as text (uuid or text PKs)
  old_row     JSONB,                -- null on INSERT
  new_row     JSONB                 -- null on DELETE
);
CREATE INDEX fin_audit_at_idx         ON memory.fin_audit (at DESC);
CREATE INDEX fin_audit_table_pk_idx   ON memory.fin_audit (table_name, row_pk);

CREATE OR REPLACE FUNCTION memory._fin_audit()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  v_old jsonb;
  v_new jsonb;
  v_pk  text;
BEGIN
  IF TG_OP <> 'INSERT' THEN v_old := to_jsonb(OLD); END IF;
  IF TG_OP <> 'DELETE' THEN v_new := to_jsonb(NEW); END IF;
  -- id (uuid tables) or key (fin_category, app_setting)
  v_pk := coalesce(v_new->>'id', v_old->>'id', v_new->>'key', v_old->>'key');
  INSERT INTO memory.fin_audit (actor, table_name, op, row_pk, old_row, new_row)
  VALUES (nullif(current_setting('app.actor', true), ''),
          TG_TABLE_NAME, TG_OP, v_pk, v_old, v_new);
  RETURN NULL;   -- AFTER trigger: return value ignored
END;
$$;

CREATE TRIGGER fin_transaction_audit AFTER INSERT OR UPDATE OR DELETE ON memory.fin_transaction
  FOR EACH ROW EXECUTE FUNCTION memory._fin_audit();
CREATE TRIGGER fin_account_audit AFTER INSERT OR UPDATE OR DELETE ON memory.fin_account
  FOR EACH ROW EXECUTE FUNCTION memory._fin_audit();
CREATE TRIGGER fin_category_audit AFTER INSERT OR UPDATE OR DELETE ON memory.fin_category
  FOR EACH ROW EXECUTE FUNCTION memory._fin_audit();
CREATE TRIGGER fin_budget_audit AFTER INSERT OR UPDATE OR DELETE ON memory.fin_budget
  FOR EACH ROW EXECUTE FUNCTION memory._fin_audit();
CREATE TRIGGER fin_holding_audit AFTER INSERT OR UPDATE OR DELETE ON memory.fin_holding
  FOR EACH ROW EXECUTE FUNCTION memory._fin_audit();
CREATE TRIGGER fin_payee_audit AFTER INSERT OR UPDATE OR DELETE ON memory.fin_payee
  FOR EACH ROW EXECUTE FUNCTION memory._fin_audit();
CREATE TRIGGER fin_asset_audit AFTER INSERT OR UPDATE OR DELETE ON memory.fin_asset
  FOR EACH ROW EXECUTE FUNCTION memory._fin_audit();
CREATE TRIGGER fin_planned_audit AFTER INSERT OR UPDATE OR DELETE ON memory.fin_planned_transaction
  FOR EACH ROW EXECUTE FUNCTION memory._fin_audit();
CREATE TRIGGER app_setting_audit AFTER INSERT OR UPDATE OR DELETE ON memory.app_setting
  FOR EACH ROW EXECUTE FUNCTION memory._fin_audit();

-- migrate:down

DROP TRIGGER IF EXISTS fin_transaction_audit ON memory.fin_transaction;
DROP TRIGGER IF EXISTS fin_account_audit ON memory.fin_account;
DROP TRIGGER IF EXISTS fin_category_audit ON memory.fin_category;
DROP TRIGGER IF EXISTS fin_budget_audit ON memory.fin_budget;
DROP TRIGGER IF EXISTS fin_holding_audit ON memory.fin_holding;
DROP TRIGGER IF EXISTS fin_payee_audit ON memory.fin_payee;
DROP TRIGGER IF EXISTS fin_asset_audit ON memory.fin_asset;
DROP TRIGGER IF EXISTS fin_planned_audit ON memory.fin_planned_transaction;
DROP TRIGGER IF EXISTS app_setting_audit ON memory.app_setting;
DROP FUNCTION IF EXISTS memory._fin_audit();
DROP TABLE IF EXISTS memory.fin_audit;
