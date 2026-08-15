-- migrate:up

-- Split memory.followup.due_at into a required date and an OPTIONAL time.
--
-- The original single TIMESTAMPTZ forced a time on every follow-up, arguing
-- that "Thursday 3pm" is one intent. That was wrong for the common case: most
-- reconnects are "some point on Tuesday", not an appointment. Requiring a time
-- makes you invent one, and a made-up 10:00 reads as a commitment you never
-- made.
--
-- This is the same shape memory.task uses (due_date DATE + due_time TIME NULL),
-- which is worth matching rather than being clever about — a follow-up with no
-- time is an all-day item, exactly like a task with no time.
--
-- Backfill converts each stored instant back to the wall-clock the user typed.
-- Asia/Bangkok is this install's timezone (PLANNER_TZ, TASK_EVENT_TZ), and the
-- values were entered through a datetime-local input, so local time is what
-- was meant.

ALTER TABLE memory.followup
  ADD COLUMN due_date DATE,
  ADD COLUMN due_time TIME;

UPDATE memory.followup
   SET due_date = (due_at AT TIME ZONE 'Asia/Bangkok')::date,
       due_time = (due_at AT TIME ZONE 'Asia/Bangkok')::time
 WHERE due_at IS NOT NULL;

ALTER TABLE memory.followup
  ALTER COLUMN due_date SET NOT NULL;

-- The hot read (what's still owed, soonest first) moves to the new columns.
DROP INDEX IF EXISTS memory.memory_followup_due_idx;

ALTER TABLE memory.followup DROP COLUMN due_at;

CREATE INDEX memory_followup_due_idx
  ON memory.followup (due_date, due_time NULLS FIRST)
  WHERE deleted_at IS NULL AND status = 'open';

-- migrate:down

ALTER TABLE memory.followup ADD COLUMN due_at TIMESTAMPTZ;

UPDATE memory.followup
   SET due_at = (due_date + COALESCE(due_time, '00:00'::time)) AT TIME ZONE 'Asia/Bangkok';

ALTER TABLE memory.followup ALTER COLUMN due_at SET NOT NULL;

DROP INDEX IF EXISTS memory.memory_followup_due_idx;

ALTER TABLE memory.followup DROP COLUMN due_date, DROP COLUMN due_time;

CREATE INDEX memory_followup_due_idx
  ON memory.followup (due_at)
  WHERE deleted_at IS NULL AND status = 'open';
