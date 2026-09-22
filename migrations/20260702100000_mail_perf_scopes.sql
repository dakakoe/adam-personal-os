-- migrate:up

-- Mail round 2 / P1 (speed): the thread list did a DISTINCT ON over the whole
-- table with a filesort (~2.4s at 29.5k messages). This expression index matches
-- the dedup ORDER BY exactly (account, thread key, newest first) so the head-of-
-- thread pass becomes an index scan, and per-thread counts become index probes.
CREATE INDEX IF NOT EXISTS gmail_message_thread_idx
  ON raw.gmail_message (account_email, (COALESCE(thread_id, message_id)), internal_date DESC);
-- Newest-first global ordering (list pages, unheadered/unscored sweeps).
CREATE INDEX IF NOT EXISTS gmail_message_date_idx
  ON raw.gmail_message (internal_date DESC);

-- Mail round 2 / P2 (send-capable accounts): the OAuth scopes Google actually
-- granted this account, captured by the web re-consent callback (token response
-- `scope` field) and a one-time tokeninfo backfill. Lets the compose From-picker
-- offer only accounts that can send (gmail.send present).
ALTER TABLE raw.gmail_account ADD COLUMN IF NOT EXISTS scopes TEXT[] NOT NULL DEFAULT '{}';

-- migrate:down

DROP INDEX IF EXISTS raw.gmail_message_thread_idx;
DROP INDEX IF EXISTS raw.gmail_message_date_idx;
ALTER TABLE raw.gmail_account DROP COLUMN IF EXISTS scopes;
