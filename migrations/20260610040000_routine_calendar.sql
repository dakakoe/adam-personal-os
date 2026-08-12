-- migrate:up

-- A routine can sync to a connected calendar as a SINGLE recurring event (RRULE),
-- not one event per day. Store which account + the recurring event's id/link.
ALTER TABLE memory.recurring_task ADD COLUMN IF NOT EXISTS gcal_account   TEXT;
ALTER TABLE memory.recurring_task ADD COLUMN IF NOT EXISTS gcal_event_id  TEXT;
ALTER TABLE memory.recurring_task ADD COLUMN IF NOT EXISTS gcal_html_link TEXT;

-- migrate:down

ALTER TABLE memory.recurring_task DROP COLUMN IF EXISTS gcal_html_link;
ALTER TABLE memory.recurring_task DROP COLUMN IF EXISTS gcal_event_id;
ALTER TABLE memory.recurring_task DROP COLUMN IF EXISTS gcal_account;
