-- migrate:up

-- Backlog #3: "suggest following a new Telegram group". The fetcher already
-- auto-discovers every group into raw.telegram_group_allowlist (enabled=false
-- until opted in). A suggestion = an unfollowed, recently-active group; this
-- column lets the owner DISMISS one so it stops being suggested.
ALTER TABLE raw.telegram_group_allowlist
  ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMPTZ;

-- migrate:down

ALTER TABLE raw.telegram_group_allowlist DROP COLUMN IF EXISTS dismissed_at;
