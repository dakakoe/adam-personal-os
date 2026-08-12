-- migrate:up

-- Phase 4 follow-up: make suggestion dedup robust to LLM title drift.
--
-- The interaction_scanner re-reads each conversation twice a day and
-- re-extracts with Haiku, which rewords titles slightly each run
-- ("…on Monday about X" vs "…about X"). The old dedup matched on exact
-- normalized title, so a reworded re-proposal slipped through and an
-- already-accepted (or dismissed) item reappeared in the inbox.
--
-- pg_trgm gives us trigram similarity() as a cheap backstop against
-- near-exact re-proposals; the scanner ALSO now feeds the model each
-- person's already-tracked items for semantic dedup (handles paraphrase
-- the trigram score misses). The GIN indexes keep similarity() lookups
-- fast as the corpus grows.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS memory_suggestion_title_trgm_idx
  ON memory.suggestion USING gin (lower(title) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS memory_task_title_trgm_idx
  ON memory.task USING gin (lower(title) gin_trgm_ops)
  WHERE deleted_at IS NULL;

-- migrate:down

DROP INDEX IF EXISTS memory.memory_task_title_trgm_idx;
DROP INDEX IF EXISTS memory.memory_suggestion_title_trgm_idx;
-- Leave the pg_trgm extension installed (other features may rely on it).
