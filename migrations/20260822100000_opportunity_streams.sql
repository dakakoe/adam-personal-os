-- migrate:up

-- Streams (deal tags) were pure free text: the vocabulary was whatever happened
-- to be typed onto a deal, so there was no way to create one up front, rename
-- one everywhere, or retire one — the tag simply reappeared with the next typo.
--
-- This registry gives streams somewhere to live independently of the deals that
-- use them. It stays deliberately thin: opportunity.tags remains the source of
-- truth for what a deal carries, and the editor treats the union of the two as
-- the vocabulary, so tags written directly onto a deal are never orphaned.
CREATE TABLE IF NOT EXISTS memory.opportunity_stream (
  name       TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed with the streams already in use so the editor opens on today's reality.
INSERT INTO memory.opportunity_stream (name)
SELECT DISTINCT unnest(tags)
  FROM memory.opportunity
 WHERE deleted_at IS NULL
ON CONFLICT (name) DO NOTHING;

-- migrate:down

DROP TABLE IF EXISTS memory.opportunity_stream;
