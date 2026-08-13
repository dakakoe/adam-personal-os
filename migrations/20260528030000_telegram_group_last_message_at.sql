-- migrate:up

-- `last_seen_at` was meant for "when did our fetcher last touch this
-- row" (bumped on every discover-groups scan + every incoming message),
-- but the /groups UI was using it as a proxy for chat activity. That
-- worked only until the first discover-groups scan, after which every
-- row showed the same recent-ish timestamp.
--
-- New column captures the chat's *own* last-message timestamp, sourced
-- from Telethon's `dialog.message.date` during discover-groups and
-- updated from `msg.date` in live ingest when an enabled chat receives
-- a message. NULL when we've never observed a message (channel that
-- hasn't posted, group that hasn't been seen in iter_dialogs yet).
ALTER TABLE raw.telegram_group_allowlist
  ADD COLUMN last_message_at TIMESTAMPTZ;

CREATE INDEX telegram_group_allowlist_last_message_idx
  ON raw.telegram_group_allowlist (last_message_at DESC NULLS LAST);

-- migrate:down

DROP INDEX IF EXISTS raw.telegram_group_allowlist_last_message_idx;
ALTER TABLE raw.telegram_group_allowlist DROP COLUMN IF EXISTS last_message_at;
