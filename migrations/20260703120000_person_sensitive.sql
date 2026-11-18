-- migrate:up
-- Sensitivity routing (Ollama expansion): an OPT-IN per-contact flag — marked
-- people's message history is only ever processed by the LOCAL LLM (profile
-- builder, interaction scanner, draft outreach route to Ollama; fail-closed).
-- Deliberately NOT visibility: that's the household-sharing ACL and defaults
-- to 'private' for ~every contact.
ALTER TABLE canonical.person ADD COLUMN IF NOT EXISTS sensitive BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS person_sensitive_idx ON canonical.person (sensitive) WHERE sensitive;

-- migrate:down
DROP INDEX IF EXISTS canonical.person_sensitive_idx;
ALTER TABLE canonical.person DROP COLUMN IF EXISTS sensitive;
