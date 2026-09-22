-- migrate:up

-- Structured identity/affiliation signals extracted from telegram bios and
-- conversation bodies via regex. Lower-confidence than canonical.identity (we
-- don't always know that a URL someone shared belongs to them) so it lives in
-- the memory layer. Twenty syncs the best signal per type per person.

CREATE TABLE memory.extracted_signal (
  id             BIGSERIAL PRIMARY KEY,
  person_id      UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  signal_type    TEXT NOT NULL,   -- 'email' | 'linkedin' | 'x' | 'instagram' | 'github' | 'website'
  value          TEXT NOT NULL,   -- normalized: email lowercased; social as bare handle; website as bare host
  confidence     TEXT NOT NULL CHECK (confidence IN ('high','medium','low')),
  source         TEXT NOT NULL,   -- 'telegram_bio' | 'conversation_outbound' | 'conversation_inbound'
  evidence       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {sample_context, interaction_ids, count}
  first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT memory_extracted_signal_unique
    UNIQUE (person_id, signal_type, value, source)
);

CREATE INDEX memory_extracted_signal_person_idx
  ON memory.extracted_signal (person_id);

CREATE INDEX memory_extracted_signal_type_idx
  ON memory.extracted_signal (signal_type);

-- migrate:down

DROP TABLE IF EXISTS memory.extracted_signal;
