-- migrate:up

-- Tiny key-value store for user-tunable knobs (first user: the /today weekly
-- completion goal). jsonb so numbers/strings/objects all fit.
CREATE TABLE IF NOT EXISTS memory.app_setting (
  key        text PRIMARY KEY,
  value      jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO memory.app_setting (key, value) VALUES ('weekly_goal', '15'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- migrate:down

DROP TABLE IF EXISTS memory.app_setting;
