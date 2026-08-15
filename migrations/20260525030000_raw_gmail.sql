-- migrate:up

-- Live-sync state per Gmail account. refresh_token + history_id are how
-- the sync worker resumes incremental fetches across restarts.
CREATE TABLE raw.gmail_account (
  email          TEXT PRIMARY KEY,
  refresh_token  TEXT,                   -- nullable for import-only accounts
  history_id     TEXT,                   -- last sync watermark from Gmail API
  last_sync_at   TIMESTAMPTZ,
  status         TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'paused' | 'reauth_needed'
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Raw message body. Lives separately from canonical.interaction so a re-run
-- of the normalizer never depends on re-pulling from Gmail.
CREATE TABLE raw.gmail_message (
  id              BIGSERIAL PRIMARY KEY,
  account_email   TEXT NOT NULL,         -- which inbox saw it (no FK on purpose: imports may precede gmail_account)
  message_id      TEXT NOT NULL,         -- Gmail API message id OR RFC822 Message-ID for imports
  thread_id       TEXT,
  rfc822_message_id TEXT,                -- always the RFC822 Message-ID (used for cross-account dedup)
  from_address    TEXT,
  from_name       TEXT,
  to_addresses    TEXT[],
  cc_addresses    TEXT[],
  bcc_addresses   TEXT[],
  subject         TEXT,
  body_text       TEXT,
  body_html       TEXT,
  internal_date   TIMESTAMPTZ NOT NULL,
  labels          TEXT[],
  source          TEXT NOT NULL,          -- 'live' | 'mbox_import'
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT raw_gmail_message_unique UNIQUE (account_email, message_id)
);

-- One person can send/receive on multiple accounts you own; the canonical
-- layer collapses these by RFC822 Message-ID. Index helps that join + helps
-- "find this message in any of my inboxes" queries.
CREATE INDEX raw_gmail_message_rfc822_idx
  ON raw.gmail_message (rfc822_message_id)
  WHERE rfc822_message_id IS NOT NULL;

CREATE INDEX raw_gmail_message_from_idx
  ON raw.gmail_message (lower(from_address))
  WHERE from_address IS NOT NULL;

CREATE INDEX raw_gmail_message_date_idx
  ON raw.gmail_message (internal_date DESC);

-- migrate:down

DROP TABLE IF EXISTS raw.gmail_message;
DROP TABLE IF EXISTS raw.gmail_account;
