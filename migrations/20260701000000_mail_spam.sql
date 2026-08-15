-- migrate:up

-- Mail auto-triage Phase B2: persisted Rspamd spam verdicts per message. merge_api
-- fetches the raw RFC822 (Gmail format=raw) and POSTs it to a self-hosted Rspamd
-- (/checkv2); the {score, action, symbols} verdict is cached here so the reader and
-- the thread list can flag spam without re-scoring. Batch-scored (POST
-- /api/mail/scan-spam), one row per scored message.
CREATE TABLE IF NOT EXISTS memory.mail_spam (
  account_email TEXT NOT NULL,
  message_id    TEXT NOT NULL,      -- raw.gmail_message.message_id
  score         REAL,               -- Rspamd numeric score
  action        TEXT,               -- no action|greylist|add header|rewrite subject|soft reject|reject
  symbols       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- Rspamd symbol map (for explanation)
  checked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (account_email, message_id)
);
-- Partial index for the "is this thread spam" lookups (reject / add-header actions).
CREATE INDEX IF NOT EXISTS mail_spam_spammy_idx ON memory.mail_spam (account_email, message_id)
  WHERE action IN ('add header', 'rewrite subject', 'reject', 'soft reject');

-- migrate:down

DROP TABLE IF EXISTS memory.mail_spam;
