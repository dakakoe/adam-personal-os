-- migrate:up

-- Curated relationship circles: family, investors, founders, friends, …
-- Self-serve like memory.opp_stage — the user defines the set; nothing here is
-- seeded, because whose circle is whose is entirely personal.
--
-- Two jobs:
--   1. curated sorting/filtering of a 12k-contact base into meaningful groups
--   2. a per-circle CADENCE — how often you mean to stay in touch. A contact
--      whose last interaction is older than their circle's cadence is "due",
--      which is what turns a passive label into something that reminds you.
CREATE TABLE memory.contact_circle (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  key           TEXT NOT NULL UNIQUE,          -- 'family' — stable, url-safe
  label         TEXT NOT NULL,                 -- 'Family' — what the UI shows
  -- Lower sorts first AND ranks higher: a person in several circles inherits
  -- the strongest (lowest priority number) one.
  priority      INT  NOT NULL DEFAULT 100,
  color         TEXT,                          -- palette key, as opp_stage.color
  -- NULL = no expectation; the circle is purely a label.
  cadence_days  INT,
  notes         TEXT,                          -- room for per-circle intent
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT contact_circle_cadence_positive CHECK (cadence_days IS NULL OR cadence_days > 0)
);

CREATE TRIGGER contact_circle_touch BEFORE UPDATE ON memory.contact_circle
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

-- Many-to-many: real relationships overlap (a friend who is also an investor).
CREATE TABLE memory.contact_circle_member (
  circle_id  UUID NOT NULL REFERENCES memory.contact_circle(id) ON DELETE CASCADE,
  person_id  UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (circle_id, person_id)
);

CREATE INDEX contact_circle_member_person_idx
  ON memory.contact_circle_member (person_id);

-- migrate:down

DROP TABLE IF EXISTS memory.contact_circle_member;
DROP TABLE IF EXISTS memory.contact_circle;
