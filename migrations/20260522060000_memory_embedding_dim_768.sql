-- migrate:up

-- Switch from OpenAI text-embedding-3-small (1536 dim) to local
-- multilingual-e5-base (768 dim). User chose local Whisper; keeping
-- the embedding stack local too — no API key, no per-row token cost,
-- multilingual content stays on-prem.
--
-- No data to migrate yet (memory.* tables are empty). HNSW indexes
-- must be dropped before changing the column type and recreated after.

DROP INDEX IF EXISTS memory.memory_interaction_embedding_hnsw;
DROP INDEX IF EXISTS memory.memory_profile_embedding_hnsw;
DROP INDEX IF EXISTS memory.memory_fact_embedding_hnsw;

ALTER TABLE memory.interaction_embedding
    ALTER COLUMN embedding TYPE vector(768),
    ALTER COLUMN embedding_model SET DEFAULT 'intfloat/multilingual-e5-base';

ALTER TABLE memory.profile
    ALTER COLUMN embedding TYPE vector(768),
    ALTER COLUMN embedding_model SET DEFAULT 'intfloat/multilingual-e5-base';

ALTER TABLE memory.fact
    ALTER COLUMN embedding TYPE vector(768),
    ALTER COLUMN embedding_model SET DEFAULT 'intfloat/multilingual-e5-base';

CREATE INDEX memory_interaction_embedding_hnsw
  ON memory.interaction_embedding
  USING hnsw (embedding vector_cosine_ops);

CREATE INDEX memory_profile_embedding_hnsw
  ON memory.profile
  USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

CREATE INDEX memory_fact_embedding_hnsw
  ON memory.fact
  USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

-- migrate:down

DROP INDEX IF EXISTS memory.memory_interaction_embedding_hnsw;
DROP INDEX IF EXISTS memory.memory_profile_embedding_hnsw;
DROP INDEX IF EXISTS memory.memory_fact_embedding_hnsw;

ALTER TABLE memory.interaction_embedding
    ALTER COLUMN embedding TYPE vector(1536),
    ALTER COLUMN embedding_model SET DEFAULT 'text-embedding-3-small';

ALTER TABLE memory.profile
    ALTER COLUMN embedding TYPE vector(1536),
    ALTER COLUMN embedding_model SET DEFAULT 'text-embedding-3-small';

ALTER TABLE memory.fact
    ALTER COLUMN embedding TYPE vector(1536),
    ALTER COLUMN embedding_model SET DEFAULT 'text-embedding-3-small';

CREATE INDEX memory_interaction_embedding_hnsw
  ON memory.interaction_embedding
  USING hnsw (embedding vector_cosine_ops);

CREATE INDEX memory_profile_embedding_hnsw
  ON memory.profile
  USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

CREATE INDEX memory_fact_embedding_hnsw
  ON memory.fact
  USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;
