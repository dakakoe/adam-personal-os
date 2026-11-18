-- migrate:up

-- Planned / recurring transactions: a TEMPLATE that materializes a concrete
-- memory.fin_transaction on each occurrence (source_kind='planned',
-- source_ref='<template id>:<date>' → idempotent). auto_post=true posts it
-- automatically on the day; false = a reminder the user posts manually.
-- Mirrors memory.recurring_task's freq/byweekday/anchor model.
CREATE TABLE memory.fin_planned_transaction (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name                TEXT,
  -- transaction template (same dual-leg shape as fin_transaction)
  outflow_account_id  UUID REFERENCES memory.fin_account(id) ON DELETE CASCADE,
  outflow_asset_id    UUID REFERENCES memory.fin_asset(id) ON DELETE SET NULL,
  outflow_amount      NUMERIC,
  inflow_account_id   UUID REFERENCES memory.fin_account(id) ON DELETE CASCADE,
  inflow_asset_id     UUID REFERENCES memory.fin_asset(id) ON DELETE SET NULL,
  inflow_amount       NUMERIC,
  category_key        TEXT REFERENCES memory.fin_category(key) ON DELETE SET NULL,
  payee_text          TEXT,
  note                TEXT,
  -- schedule
  freq                TEXT NOT NULL DEFAULT 'monthly',  -- daily|weekly|monthly|yearly
  byweekday           SMALLINT[] NOT NULL DEFAULT '{}',  -- weekly: 0=Mon … 6=Sun
  next_date           DATE NOT NULL,                     -- next occurrence to post
  auto_post           BOOLEAN NOT NULL DEFAULT true,
  active              BOOLEAN NOT NULL DEFAULT true,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at          TIMESTAMPTZ,
  CONSTRAINT fin_planned_freq_check CHECK (freq IN ('daily','weekly','monthly','yearly')),
  CONSTRAINT fin_planned_has_leg CHECK (outflow_account_id IS NOT NULL OR inflow_account_id IS NOT NULL)
);
CREATE INDEX fin_planned_due_idx ON memory.fin_planned_transaction (next_date)
  WHERE deleted_at IS NULL AND active = true;
CREATE TRIGGER fin_planned_touch BEFORE UPDATE ON memory.fin_planned_transaction
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

-- migrate:down

DROP TABLE IF EXISTS memory.fin_planned_transaction;
