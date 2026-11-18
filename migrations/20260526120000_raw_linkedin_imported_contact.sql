-- migrate:up

-- LinkedIn "Imported Contacts" — phone-book style entries the user uploaded
-- to LinkedIn at some point (often via mobile contact sync). Bigger universe
-- than Connections: has names + emails + phones, often more phones than the
-- user has anywhere else, useful for cross-channel matching with telegram.
--
-- Dedup: the importer TRUNCATEs this table before each load. The data is a
-- snapshot of the user's uploaded address book; idempotency within a run is
-- enough. canonical-side dedup happens on email/phone during normalization.

CREATE TABLE raw.linkedin_imported_contact (
  id            BIGSERIAL PRIMARY KEY,
  first_name    TEXT,
  middle_name   TEXT,
  last_name     TEXT,
  nickname      TEXT,
  prefix        TEXT,
  suffix        TEXT,
  title         TEXT,
  emails        TEXT[] NOT NULL DEFAULT '{}',
  phones        TEXT[] NOT NULL DEFAULT '{}',
  country_code  TEXT,
  region_code   TEXT,
  location      TEXT,
  source_created_at  TIMESTAMPTZ,
  source_updated_at  TIMESTAMPTZ,
  ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX raw_linkedin_imported_contact_emails_idx
  ON raw.linkedin_imported_contact USING gin (emails);

CREATE INDEX raw_linkedin_imported_contact_phones_idx
  ON raw.linkedin_imported_contact USING gin (phones);

-- migrate:down

DROP TABLE IF EXISTS raw.linkedin_imported_contact;
