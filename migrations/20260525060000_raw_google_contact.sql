-- migrate:up

-- Live Google Contacts via People API. Each row is one contact as seen by
-- one authed account; the same person can appear in multiple accounts'
-- contact books (e.g. a coworker saved in both personal + work Gmail) —
-- the normalizer collapses them via the shared email addresses.

CREATE TABLE raw.google_contact (
  id              BIGSERIAL PRIMARY KEY,
  account_email   TEXT NOT NULL,
  resource_name   TEXT NOT NULL,        -- people/c12345... — stable Google id
  etag            TEXT,                  -- for future delta sync
  display_name    TEXT,
  given_name      TEXT,
  family_name     TEXT,
  emails          TEXT[] NOT NULL DEFAULT '{}',
  phones          TEXT[] NOT NULL DEFAULT '{}',
  organization    TEXT,                  -- company name (first entry)
  job_title       TEXT,                  -- title (first entry)
  notes           TEXT,                  -- biographies[0]
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT raw_google_contact_unique UNIQUE (account_email, resource_name)
);

CREATE INDEX raw_google_contact_emails_idx
  ON raw.google_contact USING gin (emails);

CREATE INDEX raw_google_contact_phones_idx
  ON raw.google_contact USING gin (phones);

-- migrate:down

DROP TABLE IF EXISTS raw.google_contact;
