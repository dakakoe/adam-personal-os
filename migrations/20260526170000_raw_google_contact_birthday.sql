-- migrate:up

-- People API exposes birthdays via personFields=birthdays. Each contact can
-- have 0..N entries; we keep the first one with a date value (year may be
-- absent — common for privacy — so DATE column allows a default year).

ALTER TABLE raw.google_contact
  ADD COLUMN birthday DATE;

CREATE INDEX raw_google_contact_birthday_idx
  ON raw.google_contact (birthday)
  WHERE birthday IS NOT NULL;

-- migrate:down

DROP INDEX IF EXISTS raw.raw_google_contact_birthday_idx;
ALTER TABLE raw.google_contact DROP COLUMN IF EXISTS birthday;
