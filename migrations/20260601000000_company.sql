-- migrate:up

-- Company entity — ties the graph together: people ↔ companies (M2M) and
-- companies ↔ opportunities. Seeded (separately) from LLM profiles'
-- current_company + opportunity company text; names are messy so dedup is by
-- norm_name with a manual merge action rather than a hard unique constraint.

CREATE TABLE memory.company (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name         TEXT NOT NULL,
  -- lower(btrim(name)) for dedup/lookup/merge. NOT unique — seeding tolerates
  -- near-dupes, the merge action folds them.
  norm_name    TEXT NOT NULL,
  country      TEXT,
  website      TEXT,
  -- bare domain (e.g. 'acme.com') derived from website — for favicon logos
  -- and future email-domain matching.
  domain       TEXT,
  description  TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at   TIMESTAMPTZ
);

CREATE TRIGGER memory_company_touch_updated_at
  BEFORE UPDATE ON memory.company
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

CREATE INDEX memory_company_norm_idx ON memory.company (norm_name) WHERE deleted_at IS NULL;

-- Person ↔ company (many-to-many). role = job title; is_current marks
-- present vs past employment.
CREATE TABLE memory.company_person (
  company_id  UUID NOT NULL REFERENCES memory.company(id) ON DELETE CASCADE,
  person_id   UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  role        TEXT,
  is_current  BOOLEAN NOT NULL DEFAULT true,
  added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (company_id, person_id)
);

CREATE INDEX memory_company_person_person_idx ON memory.company_person (person_id);

-- Opportunity ↔ company. (The free-text opportunity.company stays for now;
-- the seed copies it into real entities and sets company_id.)
ALTER TABLE memory.opportunity
  ADD COLUMN company_id UUID REFERENCES memory.company(id) ON DELETE SET NULL;

CREATE INDEX memory_opportunity_company_idx
  ON memory.opportunity (company_id) WHERE company_id IS NOT NULL;

-- migrate:down

ALTER TABLE memory.opportunity DROP COLUMN IF EXISTS company_id;
DROP TABLE IF EXISTS memory.company_person;
DROP TABLE IF EXISTS memory.company;
