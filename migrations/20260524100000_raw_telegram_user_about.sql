-- migrate:up

-- Telegram's basic User object (returned by iter_dialogs / iter_messages) does
-- not include the free-text "About" bio. The bio is fetched via a separate
-- GetFullUser RPC, one call per user, rate-limited. We cache the result here so
-- a re-run of the enrich-bios fetcher only hits users we haven't seen yet.

ALTER TABLE raw.telegram_user
  ADD COLUMN about TEXT,
  ADD COLUMN last_full_fetch_at TIMESTAMPTZ;

CREATE INDEX raw_telegram_user_full_fetch_idx
  ON raw.telegram_user (last_full_fetch_at NULLS FIRST);

-- migrate:down

DROP INDEX IF EXISTS raw.raw_telegram_user_full_fetch_idx;
ALTER TABLE raw.telegram_user
  DROP COLUMN IF EXISTS last_full_fetch_at,
  DROP COLUMN IF EXISTS about;
