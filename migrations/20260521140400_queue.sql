-- migrate:up

CREATE TABLE queue.whisper_job (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  raw_message_id   BIGINT NOT NULL,
  voice_file_path  TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','in_progress','done','failed')),
  attempts         INT NOT NULL DEFAULT 0,
  error            TEXT,
  transcript       TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at       TIMESTAMPTZ,
  completed_at     TIMESTAMPTZ,
  CONSTRAINT queue_whisper_job_unique_raw UNIQUE (raw_message_id)
);

CREATE INDEX queue_whisper_job_poll_idx
  ON queue.whisper_job (status, created_at)
  WHERE status IN ('pending','in_progress');

CREATE TABLE queue.embedding_job (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_type   TEXT NOT NULL,    -- 'interaction' | 'profile' | 'fact'
  entity_id     UUID NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','in_progress','done','failed')),
  attempts      INT NOT NULL DEFAULT 0,
  error         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at    TIMESTAMPTZ,
  completed_at  TIMESTAMPTZ,
  CONSTRAINT queue_embedding_job_unique_entity UNIQUE (entity_type, entity_id)
);

CREATE INDEX queue_embedding_job_poll_idx
  ON queue.embedding_job (status, created_at)
  WHERE status IN ('pending','in_progress');

-- migrate:down

DROP TABLE IF EXISTS queue.embedding_job;
DROP TABLE IF EXISTS queue.whisper_job;
