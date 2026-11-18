-- migrate:up

-- Contacts exported from an iPhone as a vCard file (Contacts → Lists → long-press
-- "All Contacts" → Export, or iCloud.com → Contacts → Export vCard) and uploaded
-- on the Setup page. One row per card, keyed by the card's UID, so uploading a
-- newer export updates rows instead of duplicating them.
--
-- The phone book is where the user's OWN names for people live — including
-- people WhatsApp only knows as a number. The normalizer's iPhone pass reads
-- this table to name, enrich and create people, marking each row done in
-- processed_at; an upload that changes a card clears it so the card is redone.
--
-- No column references canonical.person, so merging people needs no
-- _MERGE_REPOINTS entry for this table.
CREATE TABLE raw.iphone_contact (
  id                  BIGSERIAL PRIMARY KEY,
  uid                 TEXT NOT NULL UNIQUE,         -- vCard UID; a content hash when the card has none
  display_name        TEXT,
  given_name          TEXT,
  family_name         TEXT,
  nickname            TEXT,
  organization        TEXT,
  job_title           TEXT,
  notes               TEXT,
  -- Apple writes year-less birthdays with the placeholder year 1604; the year
  -- is stored as-is and flagged, so nothing downstream shows "born 1604".
  birthday            DATE,
  birthday_has_year   BOOLEAN NOT NULL DEFAULT true,
  emails              TEXT[] NOT NULL DEFAULT '{}', -- lower-cased
  phones              TEXT[] NOT NULL DEFAULT '{}', -- international digits, no '+': the WhatsApp identity form
  phones_raw          TEXT[] NOT NULL DEFAULT '{}', -- exactly as written on the card
  payload             JSONB NOT NULL DEFAULT '{}'::jsonb,  -- other parsed fields (urls, addresses, social profiles); never photos
  content_hash        TEXT NOT NULL,                -- of the parsed fields: re-uploading an unchanged card is a no-op
  first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at        TIMESTAMPTZ
);

CREATE INDEX raw_iphone_contact_phones_idx ON raw.iphone_contact USING gin (phones);
CREATE INDEX raw_iphone_contact_emails_idx ON raw.iphone_contact USING gin (emails);
CREATE INDEX raw_iphone_contact_pending_idx ON raw.iphone_contact (id) WHERE processed_at IS NULL;

-- migrate:down

DROP TABLE IF EXISTS raw.iphone_contact;
