-- migrate:up

-- Who was in a meeting, resolved to a person.
--
-- Meetings were architecturally severed from the person graph: Granola recaps
-- live in memory.meeting_recap with attendees as an opaque JSONB blob that
-- nothing ever matched to canonical.identity, and calendar events the same in
-- raw.gcal_event. So the richest conversations you have — actual calls —
-- contributed nothing to a person's profile, nothing to search, and did not
-- count as contact. That last part is why the manual follow-up tick exists at
-- all: its migration comment names "a call, a coffee, WhatsApp" as the things
-- we could not see.
--
-- This table is the join that was missing. One row per (meeting, attendee).
--
-- person_id is NULLABLE on purpose, and that is a feature rather than a
-- shortcut: an attendee we cannot resolve is exactly the prompt "you had a
-- call with someone who isn't in your contacts — add them?". Those rows are
-- the queue for it, and dismissed_at remembers a no.
CREATE TABLE memory.meeting_participant (
  id              BIGSERIAL PRIMARY KEY,
  -- 'granola' | 'gcal'. Granola wins when both describe the same meeting:
  -- it carries the notes, the calendar only carries who and when.
  source          TEXT NOT NULL,
  -- memory.meeting_recap.id or raw.gcal_event.id, as text — the two have
  -- different key types (uuid vs bigint), and this column only ever needs to
  -- point back, never to join numerically.
  source_ref      TEXT NOT NULL,
  occurred_at     TIMESTAMPTZ NOT NULL,
  title           TEXT,
  attendee_email  TEXT NOT NULL,
  attendee_name   TEXT,
  person_id       UUID REFERENCES canonical.person(id) ON DELETE SET NULL,
  -- The account owner's own attendee row. Kept rather than dropped so the
  -- participant list of a meeting stays complete, but never turned into an
  -- interaction with yourself.
  is_self         BOOLEAN NOT NULL DEFAULT false,
  -- "Don't offer to add this one." Set from the UI prompt.
  dismissed_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Email is the natural key within a meeting: an attendee appears once.
  CONSTRAINT meeting_participant_unique UNIQUE (source, source_ref, attendee_email)
);

CREATE INDEX meeting_participant_person_idx
  ON memory.meeting_participant (person_id, occurred_at DESC)
  WHERE person_id IS NOT NULL;

-- The "who was that?" queue: unresolved, not dismissed, newest first.
CREATE INDEX meeting_participant_unresolved_idx
  ON memory.meeting_participant (occurred_at DESC)
  WHERE person_id IS NULL AND dismissed_at IS NULL AND is_self = false;

-- Cheap lookup when deciding whether a calendar event duplicates a Granola one.
CREATE INDEX meeting_participant_dedup_idx
  ON memory.meeting_participant (occurred_at);

-- migrate:down

DROP TABLE IF EXISTS memory.meeting_participant;
