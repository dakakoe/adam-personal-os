-- migrate:up

-- Structured JSON extract produced alongside the narrative summary by the
-- profile builder. Schema (loose; LLM may emit subset/null fields):
--   {
--     "current_company":    "string|null",
--     "current_role":       "string|null",
--     "past_companies":     ["string", ...],
--     "personal_emails":    ["string", ...],   -- emails LLM judges as theirs
--     "personal_social":    {"linkedin": "...", "x": "...", "instagram": "...",
--                            "github": "...", "website": "...", "telegram": "..."},
--     "languages":          ["string", ...]
--   }
-- Lives next to memory.profile.summary so a single Haiku call populates both
-- and they stay version-coherent.

ALTER TABLE memory.profile
  ADD COLUMN structured JSONB;

-- Common lookups: "people at Acme", "find by job title".
CREATE INDEX memory_profile_company_idx
  ON memory.profile ((structured->>'current_company'))
  WHERE structured ? 'current_company';

CREATE INDEX memory_profile_role_idx
  ON memory.profile ((structured->>'current_role'))
  WHERE structured ? 'current_role';

-- migrate:down

DROP INDEX IF EXISTS memory.memory_profile_role_idx;
DROP INDEX IF EXISTS memory.memory_profile_company_idx;
ALTER TABLE memory.profile DROP COLUMN IF EXISTS structured;
