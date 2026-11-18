-- migrate:up

-- The user's decisions about phone-book numbers written without a country
-- code ("8 916 123-45-67", "081 234 5678"), made on Cleanup → phone numbers.
-- Keyed by the number's digits as written, so one decision covers every card
-- carrying that number and survives re-uploading the phone book.
--   set    — the number is `international` (digits, no '+'); matched from now on
--   ignore — not a usable number (a short code, junk); never listed or matched
-- No column references canonical.person.
CREATE TABLE memory.phone_number_fix (
  raw_digits     TEXT PRIMARY KEY,
  action         TEXT NOT NULL CHECK (action IN ('set', 'ignore')),
  international  TEXT,
  decided_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT phone_number_fix_set_has_number
    CHECK (action <> 'set' OR international ~ '^[0-9]{8,15}$')
);

-- migrate:down

DROP TABLE IF EXISTS memory.phone_number_fix;
