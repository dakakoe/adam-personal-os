-- migrate:up

CREATE TABLE memory.profile (
  person_id                 UUID PRIMARY KEY REFERENCES canonical.person(id) ON DELETE CASCADE,
  summary                   TEXT,
  embedding                 vector(1536),
  embedding_model           TEXT NOT NULL DEFAULT 'text-embedding-3-small',
  source_interaction_count  INT NOT NULL DEFAULT 0,
  last_built_at             TIMESTAMPTZ,
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER memory_profile_touch_updated_at
  BEFORE UPDATE ON memory.profile
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

CREATE INDEX memory_profile_embedding_hnsw
  ON memory.profile
  USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

CREATE TABLE memory.fact (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  person_id              UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  fact                   TEXT NOT NULL,
  source_interaction_id  UUID REFERENCES canonical.interaction(id) ON DELETE SET NULL,
  confidence             REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
  valid_from             TIMESTAMPTZ,
  valid_until            TIMESTAMPTZ,
  embedding              vector(1536),
  embedding_model        TEXT NOT NULL DEFAULT 'text-embedding-3-small',
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX memory_fact_person_idx
  ON memory.fact (person_id);

CREATE INDEX memory_fact_embedding_hnsw
  ON memory.fact
  USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

CREATE TRIGGER memory_fact_touch_updated_at
  BEFORE UPDATE ON memory.fact
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

CREATE TABLE memory.interaction_embedding (
  interaction_id   UUID PRIMARY KEY REFERENCES canonical.interaction(id) ON DELETE CASCADE,
  embedding        vector(1536) NOT NULL,
  embedding_model  TEXT NOT NULL DEFAULT 'text-embedding-3-small',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX memory_interaction_embedding_hnsw
  ON memory.interaction_embedding
  USING hnsw (embedding vector_cosine_ops);

-- migrate:down

DROP TABLE IF EXISTS memory.interaction_embedding;
DROP TABLE IF EXISTS memory.fact;
DROP TABLE IF EXISTS memory.profile;
