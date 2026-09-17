-- migrate:up

-- One-way task → Google Calendar sync. When a task is pushed to a calendar we
-- store which connected account it landed on + the created event's id/link, so
-- later edits update the same event and completion/deletion removes it.
-- calendar is always the account's "primary" in v1 (the granted scope is
-- calendar.events — enough to write events, not to enumerate sub-calendars).
ALTER TABLE memory.task ADD COLUMN IF NOT EXISTS gcal_account   TEXT;
ALTER TABLE memory.task ADD COLUMN IF NOT EXISTS gcal_event_id  TEXT;
ALTER TABLE memory.task ADD COLUMN IF NOT EXISTS gcal_html_link TEXT;

-- migrate:down

ALTER TABLE memory.task DROP COLUMN IF EXISTS gcal_html_link;
ALTER TABLE memory.task DROP COLUMN IF EXISTS gcal_event_id;
ALTER TABLE memory.task DROP COLUMN IF EXISTS gcal_account;
