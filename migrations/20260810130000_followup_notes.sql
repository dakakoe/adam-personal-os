-- migrate:up

-- Notes on a reconnect follow-up: what you actually discussed, and what came
-- of it.
--
-- memory.followup.topic is what you meant to talk about, fixed at the moment
-- you planned it. This is the other half — a running log written as things
-- happen: "left a voice note", "he's raising, wants an intro to Dan", "moved
-- to next month".
--
-- A log rather than one editable field, mirroring memory.opportunity_event:
-- an update is a thing that happened at a time, and flattening several into
-- one blob loses when each was true. Cheap either way — a follow-up gathers a
-- handful of these at most.
CREATE TABLE memory.followup_note (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  followup_id  UUID NOT NULL REFERENCES memory.followup(id) ON DELETE CASCADE,
  body         TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Newest-first per follow-up is the only read.
CREATE INDEX memory_followup_note_followup_idx
  ON memory.followup_note (followup_id, created_at DESC);

-- migrate:down

DROP TABLE IF EXISTS memory.followup_note;
