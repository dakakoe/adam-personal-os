-- migrate:up

-- Sharing PR3 (approval workflow, transactions-only first cut). A budget member
-- EDITING or DELETING a transaction that touches a SHARED account doesn't apply
-- it directly — it's recorded here as a pending request for an owner to approve
-- (which then applies the stored action) or reject. Creating a transaction, and
-- any change to a member's OWN private items, still apply directly.
CREATE TABLE IF NOT EXISTS memory.fin_approval (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  requested_by        UUID NOT NULL REFERENCES memory.fin_member(id) ON DELETE CASCADE,
  action              TEXT NOT NULL,            -- update_txn | delete_txn
  target_table        TEXT NOT NULL DEFAULT 'fin_transaction',
  target_id           UUID NOT NULL,            -- the transaction being changed
  payload             JSONB NOT NULL DEFAULT '{}',   -- proposed patch fields (empty for delete)
  status              TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
  decided_by          UUID REFERENCES memory.fin_member(id) ON DELETE SET NULL,
  decided_at          TIMESTAMPTZ,
  note                TEXT,                     -- optional reject reason
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fin_approval_action_check CHECK (action IN ('update_txn','delete_txn')),
  CONSTRAINT fin_approval_status_check CHECK (status IN ('pending','approved','rejected'))
);

CREATE INDEX IF NOT EXISTS fin_approval_status_idx ON memory.fin_approval (status, created_at DESC);
CREATE INDEX IF NOT EXISTS fin_approval_requested_by_idx ON memory.fin_approval (requested_by);

-- approvals are a finance audit-worthy action (who asked, who decided)
CREATE TRIGGER fin_approval_audit AFTER INSERT OR UPDATE OR DELETE ON memory.fin_approval
  FOR EACH ROW EXECUTE FUNCTION memory._fin_audit();

-- migrate:down

DROP TRIGGER IF EXISTS fin_approval_audit ON memory.fin_approval;
DROP TABLE IF EXISTS memory.fin_approval;
