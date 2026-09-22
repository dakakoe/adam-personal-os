-- migrate:up

-- Phase 5b: draft outreach. Generate a follow-up message for a contact
-- (Haiku, grounded in the recent thread + an open task/opp), store it for
-- review, edit, copy. NOTHING is ever auto-sent — 'sent' is only ever set
-- by an explicit user action (and actual channel send stays out until the
-- gmail.send / Telegram path is re-consented). Mirrors the suggestion
-- inbox's approval-strict pattern.

CREATE TABLE memory.draft (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  person_id       UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  channel         TEXT NOT NULL DEFAULT 'telegram',     -- 'telegram' | 'email'
  subject         TEXT,                                 -- email only
  body            TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'draft',        -- 'draft' | 'sent' | 'discarded'
  -- What the draft was about (provenance / context).
  task_id         UUID REFERENCES memory.task(id) ON DELETE SET NULL,
  opportunity_id  UUID REFERENCES memory.opportunity(id) ON DELETE SET NULL,
  model           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at      TIMESTAMPTZ,
  CONSTRAINT draft_channel_check CHECK (channel IN ('telegram', 'email')),
  CONSTRAINT draft_status_check  CHECK (status  IN ('draft', 'sent', 'discarded'))
);

CREATE TRIGGER memory_draft_touch_updated_at
  BEFORE UPDATE ON memory.draft
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

CREATE INDEX memory_draft_person_idx
  ON memory.draft (person_id, created_at DESC);

-- migrate:down

DROP TABLE IF EXISTS memory.draft;
