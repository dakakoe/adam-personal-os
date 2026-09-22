-- migrate:up

-- Sub-calendar targets for task/routine → calendar sync. NULL = the account's
-- primary calendar (the only option before this migration).
ALTER TABLE memory.task           ADD COLUMN IF NOT EXISTS gcal_calendar_id text;
ALTER TABLE memory.recurring_task ADD COLUMN IF NOT EXISTS gcal_calendar_id text;

-- migrate:down

ALTER TABLE memory.recurring_task DROP COLUMN IF EXISTS gcal_calendar_id;
ALTER TABLE memory.task           DROP COLUMN IF EXISTS gcal_calendar_id;
