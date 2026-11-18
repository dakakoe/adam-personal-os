-- migrate:up

CREATE TABLE canonical.person (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name  TEXT NOT NULL,
  notes         TEXT,
  merged_into   UUID REFERENCES canonical.person(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT canonical_person_no_self_merge CHECK (merged_into IS DISTINCT FROM id)
);

CREATE INDEX canonical_person_merged_into_idx
  ON canonical.person (merged_into)
  WHERE merged_into IS NOT NULL;

CREATE TRIGGER canonical_person_touch_updated_at
  BEFORE UPDATE ON canonical.person
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

CREATE TABLE canonical.identity (
  id          BIGSERIAL PRIMARY KEY,
  person_id   UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  source      TEXT NOT NULL,    -- 'telegram' | 'phone' | 'email' | 'gmail' | ...
  source_id   TEXT NOT NULL,
  evidence    JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT canonical_identity_unique UNIQUE (source, source_id)
);

CREATE INDEX canonical_identity_person_idx
  ON canonical.identity (person_id);

CREATE TABLE canonical.interaction (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  person_id    UUID REFERENCES canonical.person(id) ON DELETE SET NULL,
  channel      TEXT NOT NULL,   -- 'telegram_text' | 'telegram_voice' | 'gmail' | ...
  direction    TEXT NOT NULL CHECK (direction IN ('inbound','outbound')),
  occurred_at  TIMESTAMPTZ NOT NULL,
  body         TEXT,
  raw_source   TEXT NOT NULL,   -- e.g. 'raw.telegram_message'
  raw_id       BIGINT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT canonical_interaction_unique_raw UNIQUE (raw_source, raw_id)
);

CREATE INDEX canonical_interaction_person_time_idx
  ON canonical.interaction (person_id, occurred_at DESC)
  WHERE person_id IS NOT NULL;

CREATE INDEX canonical_interaction_time_idx
  ON canonical.interaction (occurred_at DESC);

CREATE INDEX canonical_interaction_channel_idx
  ON canonical.interaction (channel);

-- migrate:down

DROP TABLE IF EXISTS canonical.interaction;
DROP TABLE IF EXISTS canonical.identity;
DROP TABLE IF EXISTS canonical.person;
