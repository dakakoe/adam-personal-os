-- migrate:up

-- Mail Phase 3: a LOCAL, app-side state overlay for the read-only mail client —
-- archive (declutter the app view) + star (flag for follow-up), keyed per thread
-- per account. Deliberately app-only: it does NOT mutate Gmail (that would need
-- the gmail.modify scope) — it's CRMS's own triage layer over the ingested mail.
CREATE TABLE IF NOT EXISTS memory.mail_state (
  account_email TEXT NOT NULL,
  thread_key    TEXT NOT NULL,   -- COALESCE(thread_id, message_id), matches the reader
  archived      BOOLEAN NOT NULL DEFAULT false,
  starred       BOOLEAN NOT NULL DEFAULT false,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (account_email, thread_key)
);
CREATE INDEX IF NOT EXISTS mail_state_starred_idx ON memory.mail_state (starred) WHERE starred;

-- migrate:down

DROP TABLE IF EXISTS memory.mail_state;
