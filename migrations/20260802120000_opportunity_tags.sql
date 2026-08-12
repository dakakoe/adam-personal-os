-- migrate:up

-- Deals span unrelated streams — BD partnerships, a job hunt, a specific venture
-- — that share one board and one stage vocabulary. Tags let a deal carry several
-- labels ('job', 'consulting', …) so the board/list can be filtered per stream
-- without splitting the pipeline or duplicating stage config.
--
-- Free-form TEXT[] rather than a lookup table: the tag set is small, personal,
-- and evolves ad hoc; the distinct-tags endpoint derives the vocabulary from the
-- data itself, so there's nothing to keep in sync.
ALTER TABLE memory.opportunity
  ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';

-- GIN supports the `tags && ARRAY[...]` overlap test the filter uses.
CREATE INDEX IF NOT EXISTS memory_opportunity_tags_idx
  ON memory.opportunity USING GIN (tags);

-- migrate:down

DROP INDEX IF EXISTS memory.memory_opportunity_tags_idx;
ALTER TABLE memory.opportunity DROP COLUMN IF EXISTS tags;
