-- migrate:up

-- Reconnect follow-ups: "talk to this person, by then, about that".
--
-- The gap this fills sits between two things that already exist:
--   * contact circles give a CADENCE — a standing rhythm per group, which
--     surfaces people automatically but says nothing about a specific
--     conversation you owe someone;
--   * memory.opportunity tracks a DEAL — which needs a deal to exist first.
-- A follow-up is neither: a person you mean to reconnect with, on a date, for
-- a reason, before there's anything to call a deal.
--
-- Deliberately NOT memory.task: a task is something you do, a follow-up is a
-- conversation you owe. Keeping them apart stops reconnects drowning in a
-- to-do list, and lets the "did it happen" rule below apply to all of these
-- and none of those.
CREATE TABLE memory.followup (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  person_id     UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  -- Date AND time in one column: "Thursday 3pm" is one intent, and splitting
  -- it into two nullable fields (as memory.task does) invites half-set rows.
  due_at        TIMESTAMPTZ NOT NULL,
  topic         TEXT,                        -- what you mean to discuss
  status        TEXT NOT NULL DEFAULT 'open',
  -- Set ONLY by a manual tick — for a conversation that happened somewhere we
  -- can't see (a call, a coffee, WhatsApp). A conversation on a channel we DO
  -- ingest is derived at read time from canonical.interaction instead of being
  -- written here, so it can't go stale and needs no worker to maintain it.
  connected_at  TIMESTAMPTZ,
  connected_via TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at    TIMESTAMPTZ,
  CONSTRAINT followup_status_check CHECK (status IN ('open', 'connected', 'cancelled')),
  -- A manual tick must say when; a row without one must not claim a channel.
  CONSTRAINT followup_connected_coherent CHECK (
    (connected_at IS NOT NULL) OR (connected_via IS NULL)
  )
);

CREATE TRIGGER memory_followup_touch_updated_at
  BEFORE UPDATE ON memory.followup
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

CREATE INDEX memory_followup_person_idx
  ON memory.followup (person_id);

-- The pipeline's hot read: what's still open, soonest first.
CREATE INDEX memory_followup_due_idx
  ON memory.followup (due_at)
  WHERE deleted_at IS NULL AND status = 'open';

-- migrate:down

DROP TABLE IF EXISTS memory.followup;
