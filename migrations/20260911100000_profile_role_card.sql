-- migrate:up

-- A short ROLE CARD per person, ranked separately from the profile paragraph.
-- Measured against the user's circles (gap 3, 2026-09-11): the paragraph is
-- five sentences of relationship narrative with the role in one of them, and
-- ranks 14 circle members into the top 50 for real "who can help" needs; the
-- card — name, the summary's lead sentence, LinkedIn and company role — ranks
-- 28. Blending the two was worse than the card alone, so it gets its own
-- vector rather than replacing or mixing into `embedding`.
--
-- Composed by the profile builder, never generated: no LLM cost. Lives on
-- memory.profile, so merging people needs no new _MERGE_REPOINTS entry.
ALTER TABLE memory.profile
  ADD COLUMN card_text      TEXT,
  ADD COLUMN card_embedding vector(768);

CREATE INDEX memory_profile_card_hnsw
  ON memory.profile
  USING hnsw (card_embedding vector_cosine_ops)
  WHERE card_embedding IS NOT NULL;

-- migrate:down

DROP INDEX IF EXISTS memory.memory_profile_card_hnsw;
ALTER TABLE memory.profile
  DROP COLUMN IF EXISTS card_embedding,
  DROP COLUMN IF EXISTS card_text;
