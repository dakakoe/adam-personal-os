-- migrate:up

-- How long a timed task / routine occurrence lasts, in minutes. Drives the
-- calendar event's end (start + duration); null = the 60-min default. Ignored
-- for all-day items.
ALTER TABLE memory.task           ADD COLUMN IF NOT EXISTS duration_min INT;
ALTER TABLE memory.recurring_task ADD COLUMN IF NOT EXISTS duration_min INT;

-- migrate:down

ALTER TABLE memory.recurring_task DROP COLUMN IF EXISTS duration_min;
ALTER TABLE memory.task           DROP COLUMN IF EXISTS duration_min;
