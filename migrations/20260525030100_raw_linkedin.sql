-- migrate:up

-- LinkedIn data exports are CSV-shaped; we mirror that shape into raw and
-- leave the normalization for a separate pass that maps connections to
-- canonical.person and messages to canonical.interaction.

CREATE TABLE raw.linkedin_connection (
  id            BIGSERIAL PRIMARY KEY,
  first_name    TEXT,
  last_name     TEXT,
  email         TEXT,
  company       TEXT,
  position      TEXT,
  url           TEXT,             -- linkedin.com/in/<vanity>
  connected_on  DATE,
  ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT raw_linkedin_connection_unique UNIQUE (url)
);

CREATE INDEX raw_linkedin_connection_email_idx
  ON raw.linkedin_connection (lower(email))
  WHERE email IS NOT NULL;

CREATE TABLE raw.linkedin_message (
  id                BIGSERIAL PRIMARY KEY,
  conversation_id   TEXT,
  conversation_title TEXT,
  from_name         TEXT,
  from_profile_url  TEXT,
  to_names          TEXT[],
  to_profile_urls   TEXT[],
  message_date      TIMESTAMPTZ NOT NULL,
  subject           TEXT,
  content           TEXT,
  folder            TEXT,           -- 'INBOX' | 'SENT' | etc.
  ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT raw_linkedin_message_unique UNIQUE (conversation_id, message_date, from_profile_url)
);

CREATE INDEX raw_linkedin_message_conv_idx
  ON raw.linkedin_message (conversation_id, message_date DESC);

CREATE INDEX raw_linkedin_message_from_idx
  ON raw.linkedin_message (from_profile_url);

-- migrate:down

DROP TABLE IF EXISTS raw.linkedin_message;
DROP TABLE IF EXISTS raw.linkedin_connection;
