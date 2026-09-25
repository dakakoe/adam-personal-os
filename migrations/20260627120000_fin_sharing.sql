-- migrate:up

-- Sharing PR1: dynamic members + per-account visibility (shared-by-default).
-- Replaces the hardcoded me|wife|son owner enum + two-token role model with a
-- members table tied to Authelia identities, and lets an account be marked
-- private to its owning member. Per-category ACL and an approval workflow are
-- deliberately out of scope (PR2 / PR3).

CREATE TABLE IF NOT EXISTS memory.fin_member (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name  TEXT NOT NULL,
  email         TEXT UNIQUE,            -- Authelia Remote-Email / Remote-User
  person_id     UUID REFERENCES canonical.person(id) ON DELETE SET NULL,
  role          TEXT NOT NULL DEFAULT 'member',  -- owner (full access) | member (scoped)
  actor         TEXT NOT NULL UNIQUE,  -- fin_audit actor string (owner/member/…)
  is_active     BOOLEAN NOT NULL DEFAULT true,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fin_member_role_check CHECK (role IN ('owner','member'))
);

-- members are owner-managed config → audited like the other fin_* tables
CREATE TRIGGER fin_member_audit AFTER INSERT OR UPDATE OR DELETE ON memory.fin_member
  FOR EACH ROW EXECUTE FUNCTION memory._fin_audit();

-- account-level sharing: visible to everyone (shared) or only its owner + an
-- app-owner (private). owner_member_id supersedes the legacy `owner` enum, which
-- is kept (vestigial, still defaulted) for back-compat until a later cleanup.
ALTER TABLE memory.fin_account
  ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'shared',
  ADD COLUMN IF NOT EXISTS owner_member_id UUID REFERENCES memory.fin_member(id) ON DELETE SET NULL;
ALTER TABLE memory.fin_account DROP CONSTRAINT IF EXISTS fin_account_visibility_check;
ALTER TABLE memory.fin_account
  ADD CONSTRAINT fin_account_visibility_check CHECK (visibility IN ('shared','private'));
CREATE INDEX IF NOT EXISTS fin_account_owner_member_idx ON memory.fin_account (owner_member_id);

-- seed example members: an owner, a member, and a guest (
-- inactive until he gets a login). person_id is linked later from the members
-- admin UI (a migration can't reliably resolve a canonical.person by name).
INSERT INTO memory.fin_member (display_name, role, actor, is_active) VALUES
  ('Owner',  'owner',  'owner',  true),
  ('Member', 'member', 'member', true),
  ('Guest',  'member', 'guest',  false)
ON CONFLICT (actor) DO NOTHING;

-- backfill the new owner FK from the legacy enum
UPDATE memory.fin_account a SET owner_member_id = m.id
  FROM memory.fin_member m
 WHERE a.owner_member_id IS NULL
   AND m.actor = CASE a.owner WHEN 'me' THEN 'owner' WHEN 'wife' THEN 'member'
                              WHEN 'son' THEN 'guest' END;

-- migrate:down

ALTER TABLE memory.fin_account DROP CONSTRAINT IF EXISTS fin_account_visibility_check;
DROP INDEX IF EXISTS memory.fin_account_owner_member_idx;
ALTER TABLE memory.fin_account DROP COLUMN IF EXISTS owner_member_id;
ALTER TABLE memory.fin_account DROP COLUMN IF EXISTS visibility;
DROP TRIGGER IF EXISTS fin_member_audit ON memory.fin_member;
DROP TABLE IF EXISTS memory.fin_member;
