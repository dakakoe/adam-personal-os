-- migrate:up

-- When was this actually discussed? A suggestion's created_at is when the
-- scanner/ingest ran, NOT when the conversation happened. The scanner reads
-- the last ~20 messages of a thread, which can span months — so a task it
-- surfaces today might have been discussed weeks ago. context_at records the
-- real discussion time so the inbox can show "discussed 2mo ago" and the user
-- can judge staleness.
--   - interaction: the date the item was discussed (Haiku-picked from the fed
--     message timestamps; falls back to the thread's latest message).
--   - granola: the meeting_date.

ALTER TABLE memory.suggestion ADD COLUMN context_at TIMESTAMPTZ;

-- Backfill granola suggestions from their meeting recap.
UPDATE memory.suggestion s
   SET context_at = mr.meeting_date
  FROM memory.meeting_recap mr
 WHERE s.source_kind = 'granola'
   AND mr.id::text = s.source_ref
   AND s.context_at IS NULL;

-- migrate:down

ALTER TABLE memory.suggestion DROP COLUMN IF EXISTS context_at;
