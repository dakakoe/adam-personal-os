-- migrate:up

-- Participants on a routine: people who should be INVITED to the recurring
-- calendar event. They ride the single RRULE series event (upsert_recurring_event),
-- NOT the per-day materialized task instances — so Google sends ONE invite for
-- the whole series, not one per occurrence. Only participants that resolve to a
-- non-sensitive email are actually invited; the rest are still tracked here.
CREATE TABLE memory.recurring_task_participant (
  recurring_task_id UUID NOT NULL REFERENCES memory.recurring_task(id) ON DELETE CASCADE,
  person_id         UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (recurring_task_id, person_id)
);

CREATE INDEX recurring_task_participant_person_idx
  ON memory.recurring_task_participant (person_id);

-- migrate:down

DROP TABLE IF EXISTS memory.recurring_task_participant;
