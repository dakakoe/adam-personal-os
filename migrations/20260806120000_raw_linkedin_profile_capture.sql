-- migrate:up

-- Profiles captured live from a logged-in LinkedIn tab by the browser
-- extension (browser_ext/linkedin_capture). LinkedIn has no public API and
-- blocks server-side fetching, so the page the user is already looking at is
-- the only place this data exists. Landing zone plays the same role
-- raw.linkedin_connection plays for the CSV export — one row per vanity,
-- normalization into canonical happens in merge_api.capture_linkedin_profile.
--
-- Columns are a superset of raw.linkedin_connection (first/last/company/
-- position/url) plus what the page shows and the CSV doesn't: headline,
-- location, about, avatar, connection degree, and the contact-info overlay's
-- emails/phones/websites/twitter/birthday.
--
-- Dedup: UNIQUE (vanity) + upsert. Re-capturing a profile refreshes it in
-- place — a profile changes over time and the newest read is the truth — so
-- pressing the button twice costs a timestamp and nothing else.

CREATE TABLE raw.linkedin_profile_capture (
  id                BIGSERIAL PRIMARY KEY,
  vanity            TEXT NOT NULL,   -- linkedin.com/in/<vanity>, lowercased
  profile_url       TEXT,
  full_name         TEXT,
  first_name        TEXT,
  last_name         TEXT,
  headline          TEXT,
  location          TEXT,
  about             TEXT,
  current_title     TEXT,
  current_company   TEXT,
  company_url       TEXT,
  avatar_url        TEXT,
  connection_degree TEXT,            -- '1st' | '2nd' | '3rd' | 'out of network'
  emails            TEXT[] NOT NULL DEFAULT '{}',
  phones            TEXT[] NOT NULL DEFAULT '{}',
  websites          TEXT[] NOT NULL DEFAULT '{}',
  twitter           TEXT,
  birthday          TEXT,            -- LinkedIn shows a year-less "March 14"
  experience        JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{title, company, date_range, location}]
  education         JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{school, degree, date_range}]
  captured_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT raw_linkedin_profile_capture_unique UNIQUE (vanity)
);

CREATE INDEX raw_linkedin_profile_capture_emails_idx
  ON raw.linkedin_profile_capture USING gin (emails);

CREATE INDEX raw_linkedin_profile_capture_captured_idx
  ON raw.linkedin_profile_capture (captured_at DESC);

-- migrate:down

DROP TABLE IF EXISTS raw.linkedin_profile_capture;
