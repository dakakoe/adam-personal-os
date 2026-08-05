-- migrate:up

-- Queue of "these two persons might be the same" hypotheses. Generators
-- (LLM-verified emails, fuzzy name, phone match, future profile-embedding
-- and email-thread co-occurrence) write candidates here. The merge UI reads
-- pending rows and lets the user approve / reject / defer.

CREATE TABLE memory.merge_candidate (
  id              BIGSERIAL PRIMARY KEY,
  left_person_id  UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  right_person_id UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  source          TEXT NOT NULL,
    -- 'llm_email' | 'fuzzy_name' | 'phone_match' | 'profile_embedding' | 'email_thread' | 'manual'
  confidence      TEXT NOT NULL CHECK (confidence IN ('high','medium','low')),
  score           REAL,
  evidence        JSONB NOT NULL DEFAULT '{}'::jsonb,
  status          TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','approved','rejected','deferred','auto_merged')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at      TIMESTAMPTZ,
  decided_by      TEXT,
  decision_note   TEXT,
  CONSTRAINT merge_candidate_no_self CHECK (left_person_id <> right_person_id)
);

-- Postgres UNIQUE constraints can't reference functions; use a UNIQUE
-- INDEX so (A,B) and (B,A) collapse to the same key.
CREATE UNIQUE INDEX merge_candidate_unique_pair_idx
  ON memory.merge_candidate (LEAST(left_person_id, right_person_id),
                             GREATEST(left_person_id, right_person_id));

CREATE INDEX memory_merge_candidate_status_idx
  ON memory.merge_candidate (status, confidence, created_at DESC);

CREATE INDEX memory_merge_candidate_left_idx
  ON memory.merge_candidate (left_person_id);

CREATE INDEX memory_merge_candidate_right_idx
  ON memory.merge_candidate (right_person_id);

-- migrate:down

DROP TABLE IF EXISTS memory.merge_candidate;
