-- migrate:up

-- Self-serve deal stages: replace the fixed memory.opportunity_stage enum
-- with a config table + plain-text stage columns, so stages can be added /
-- renamed / reordered from the UI without DDL.

CREATE TABLE IF NOT EXISTS memory.opp_stage (
  key        text PRIMARY KEY,                  -- stable slug ('intro', 'mou', …)
  label      text NOT NULL,                     -- display name ('Proposal')
  sort       int  NOT NULL,                     -- board column order
  terminal   boolean NOT NULL DEFAULT false,    -- lost-like: out of the live pipeline, muted in UI
  closes     boolean NOT NULL DEFAULT false,    -- entering this stage sets closed_at (won or lost)
  color      text NOT NULL DEFAULT 'slate',     -- UI accent (palette name)
  created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO memory.opp_stage (key, label, sort, terminal, closes, color) VALUES
  ('intro',         'Intro',         1, false, false, 'sky'),
  ('conversations', 'Conversations', 2, false, false, 'violet'),
  ('mou',           'Proposal',      3, false, false, 'amber'),
  ('contract',      'Contract',      4, false, false, 'orange'),
  ('active',        'Active',        5, false, true,  'emerald'),
  ('lost',          'Lost',         99, true,  true,  'slate')
ON CONFLICT (key) DO NOTHING;

-- enum columns → text (values are unchanged; the index on stage rebuilds)
ALTER TABLE memory.opportunity ALTER COLUMN stage DROP DEFAULT;
ALTER TABLE memory.opportunity ALTER COLUMN stage TYPE text USING stage::text;
ALTER TABLE memory.opportunity ALTER COLUMN stage SET DEFAULT 'intro';
ALTER TABLE memory.opportunity
  ADD CONSTRAINT opportunity_stage_fk FOREIGN KEY (stage) REFERENCES memory.opp_stage(key);

-- history + suggestions keep loose text (no FK — old keys may outlive config)
ALTER TABLE memory.opportunity_event ALTER COLUMN from_stage TYPE text USING from_stage::text;
ALTER TABLE memory.opportunity_event ALTER COLUMN to_stage   TYPE text USING to_stage::text;
ALTER TABLE memory.suggestion ALTER COLUMN suggested_stage TYPE text USING suggested_stage::text;

DROP TYPE IF EXISTS memory.opportunity_stage;

-- migrate:down

CREATE TYPE memory.opportunity_stage AS ENUM
  ('intro', 'conversations', 'mou', 'contract', 'active', 'lost');
ALTER TABLE memory.opportunity DROP CONSTRAINT IF EXISTS opportunity_stage_fk;
ALTER TABLE memory.opportunity ALTER COLUMN stage DROP DEFAULT;
ALTER TABLE memory.opportunity ALTER COLUMN stage TYPE memory.opportunity_stage USING stage::memory.opportunity_stage;
ALTER TABLE memory.opportunity ALTER COLUMN stage SET DEFAULT 'intro';
ALTER TABLE memory.opportunity_event ALTER COLUMN from_stage TYPE memory.opportunity_stage USING from_stage::memory.opportunity_stage;
ALTER TABLE memory.opportunity_event ALTER COLUMN to_stage   TYPE memory.opportunity_stage USING to_stage::memory.opportunity_stage;
ALTER TABLE memory.suggestion ALTER COLUMN suggested_stage TYPE memory.opportunity_stage USING suggested_stage::memory.opportunity_stage;
DROP TABLE IF EXISTS memory.opp_stage;
