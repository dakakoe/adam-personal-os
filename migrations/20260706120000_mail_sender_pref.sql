-- migrate:up

-- Per-sender cleanup preference for the /mail/cleanup page. A row here marks a
-- sender the user wants to KEEP — it is excluded from the cleanup recommendations
-- and never auto-selected by "Select recommended", even if it otherwise looks
-- like automated noise (has an unsubscribe link, is a newsletter, etc.). Keyed by
-- from_address (account-agnostic: the same newsletter across inboxes is one row).
CREATE TABLE IF NOT EXISTS memory.mail_sender_pref (
  from_address TEXT PRIMARY KEY,
  keep         BOOLEAN NOT NULL DEFAULT true,  -- true = keep, don't recommend for cleanup
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down

DROP TABLE IF EXISTS memory.mail_sender_pref;
