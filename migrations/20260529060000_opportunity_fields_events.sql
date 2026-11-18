-- migrate:up

-- Opportunity card redesign (Phase 5b): richer deals + a status history.
--   - company: free-text counterparty company (the contact is the person,
--     the company is the org).
--   - award_usd + award_note: the old free-text estimated_value is split
--     into a numeric USD amount (for sorting/sums later) AND a free-text
--     outcome ("MoU signed", "intro to KBW") for non-money wins. Existing
--     estimated_value is migrated into award_note; the column stays for
--     backward-compat (suggestion-accept still sets it).
--   - responsible_person_id: who owns the deal. NULL = "Me".
--   - opportunity_event: the timeline. Every stage change records an event
--     with the next step; freeform notes use kind='note'.

ALTER TABLE memory.opportunity
  ADD COLUMN company               TEXT,
  ADD COLUMN award_usd             NUMERIC,
  ADD COLUMN award_note            TEXT,
  ADD COLUMN responsible_person_id UUID REFERENCES canonical.person(id) ON DELETE SET NULL;

UPDATE memory.opportunity
   SET award_note = estimated_value
 WHERE estimated_value IS NOT NULL AND btrim(estimated_value) <> '';

CREATE INDEX memory_opportunity_responsible_idx
  ON memory.opportunity (responsible_person_id) WHERE responsible_person_id IS NOT NULL;

CREATE TABLE memory.opportunity_event (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id  UUID NOT NULL REFERENCES memory.opportunity(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL DEFAULT 'stage_change',  -- 'stage_change' | 'note'
  from_stage      memory.opportunity_stage,
  to_stage        memory.opportunity_stage,
  next_step       TEXT,
  note            TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT opportunity_event_kind_check CHECK (kind IN ('stage_change', 'note'))
);

CREATE INDEX memory_opportunity_event_opp_idx
  ON memory.opportunity_event (opportunity_id, created_at DESC);

-- Seed an opening "created at <stage>" event for every existing live
-- opportunity so the timeline isn't empty on first open.
INSERT INTO memory.opportunity_event (opportunity_id, kind, from_stage, to_stage, note, created_at)
SELECT o.id, 'stage_change', NULL, o.stage, 'Opportunity created', o.created_at
  FROM memory.opportunity o
 WHERE o.deleted_at IS NULL;

-- migrate:down

DROP TABLE IF EXISTS memory.opportunity_event;
ALTER TABLE memory.opportunity
  DROP COLUMN IF EXISTS responsible_person_id,
  DROP COLUMN IF EXISTS award_note,
  DROP COLUMN IF EXISTS award_usd,
  DROP COLUMN IF EXISTS company;
