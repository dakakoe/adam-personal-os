-- migrate:up

-- Unified-sharing — contacts. Apply the same per-row ACL primitive (visibility +
-- owner_member_id, scoped by queries.visibility_clause) to contacts and companies.
-- Default is 'private' (not 'shared' like budget accounts): members currently see
-- ZERO CRM, so contacts stay owner-only until explicitly shared — a deliberate,
-- minimal opening. Columns are added to both tables now; this PR scopes persons,
-- companies follow.
ALTER TABLE canonical.person
  ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'private',
  ADD COLUMN IF NOT EXISTS owner_member_id UUID REFERENCES memory.fin_member(id) ON DELETE SET NULL;
ALTER TABLE canonical.person DROP CONSTRAINT IF EXISTS person_visibility_check;
ALTER TABLE canonical.person
  ADD CONSTRAINT person_visibility_check CHECK (visibility IN ('shared','private'));
CREATE INDEX IF NOT EXISTS person_owner_member_idx ON canonical.person (owner_member_id);

ALTER TABLE memory.company
  ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'private',
  ADD COLUMN IF NOT EXISTS owner_member_id UUID REFERENCES memory.fin_member(id) ON DELETE SET NULL;
ALTER TABLE memory.company DROP CONSTRAINT IF EXISTS company_visibility_check;
ALTER TABLE memory.company
  ADD CONSTRAINT company_visibility_check CHECK (visibility IN ('shared','private'));
CREATE INDEX IF NOT EXISTS company_owner_member_idx ON memory.company (owner_member_id);

-- migrate:down

ALTER TABLE memory.company DROP CONSTRAINT IF EXISTS company_visibility_check;
DROP INDEX IF EXISTS memory.company_owner_member_idx;
ALTER TABLE memory.company DROP COLUMN IF EXISTS owner_member_id;
ALTER TABLE memory.company DROP COLUMN IF EXISTS visibility;

ALTER TABLE canonical.person DROP CONSTRAINT IF EXISTS person_visibility_check;
DROP INDEX IF EXISTS canonical.person_owner_member_idx;
ALTER TABLE canonical.person DROP COLUMN IF EXISTS owner_member_id;
ALTER TABLE canonical.person DROP COLUMN IF EXISTS visibility;
