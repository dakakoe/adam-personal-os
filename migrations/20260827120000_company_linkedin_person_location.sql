-- migrate:up

-- Two additive fields for the company/location backfill and the search it
-- feeds:
--   * memory.company.linkedin_url — the company's LinkedIn page, taken from a
--     captured profile's company_url. Lets the People→company chip and the
--     company page link straight out to LinkedIn.
--   * canonical.person.location — a first-class, EDITABLE location. It was only
--     ever derived read-only from a LinkedIn capture on the contact page, so it
--     couldn't be searched, filtered, or corrected. The backfill seeds it; a
--     hand edit then wins.
ALTER TABLE memory.company   ADD COLUMN IF NOT EXISTS linkedin_url TEXT;
ALTER TABLE canonical.person ADD COLUMN IF NOT EXISTS location     TEXT;

-- Trigram search: People search matches a person's company name and location
-- with ILIKE '%…%', which without a trigram index is a full scan on every
-- keystroke. pg_trgm is already enabled (the fuzzy name resolver uses it).
CREATE INDEX IF NOT EXISTS memory_company_name_trgm
  ON memory.company USING gin (lower(name) gin_trgm_ops)
  WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS canonical_person_location_trgm
  ON canonical.person USING gin (lower(location) gin_trgm_ops)
  WHERE location IS NOT NULL AND deleted_at IS NULL;

-- migrate:down

DROP INDEX IF EXISTS canonical.canonical_person_location_trgm;
DROP INDEX IF EXISTS memory.memory_company_name_trgm;
ALTER TABLE canonical.person DROP COLUMN IF EXISTS location;
ALTER TABLE memory.company   DROP COLUMN IF EXISTS linkedin_url;
