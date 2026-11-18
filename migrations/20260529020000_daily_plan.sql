-- migrate:up

-- Phase 4 (v1) of the personal-OS expansion: a droplet-native daily
-- planner. A worker (daily_planner/) synthesizes everything already on
-- the droplet — open tasks, live opportunities, pending suggestions,
-- recent meeting recaps, and who's owed a reply — into a punchy
-- prioritized narrative + a structured focus list. The /today view is
-- the first thing the owner sees each morning.
--
-- This moves the "nightly-plan-tomorrow" job out of Cowork (Claude
-- Desktop must be open) onto the always-on droplet. v2 will add Google
-- Calendar once the owner re-consents to the calendar scope.

-- One row per day. Regenerating overwrites the row for that date
-- (ON CONFLICT (plan_date)), so no soft-delete is needed — the latest
-- generation for a date is the only one that matters.
CREATE TABLE memory.daily_plan (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- The day this plan is *for* (not when it was generated). The nightly
  -- timer plans for tomorrow; a manual regenerate during the day plans
  -- for today. /today reads the most recent plan_date.
  plan_date     DATE NOT NULL UNIQUE,
  -- Haiku's prioritized narrative (markdown, terse — the owner's style).
  narrative     TEXT,
  -- Structured payload: the focus list + the raw input snapshots the UI
  -- renders without re-querying. Shape:
  --   { "focus":   [ {title, reason, kind, ref_id, project_slug} ],
  --     "counts":  {open_tasks, live_opps, pending_suggestions, owes_reply},
  --     "tasks":   [...], "opportunities": [...], "owes_reply": [...],
  --     "recaps":  [...], "model": "claude-haiku-4-5" }
  structured    JSONB NOT NULL DEFAULT '{}'::jsonb,
  generated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- /today's lookup: latest plan first.
CREATE INDEX memory_daily_plan_date_idx
  ON memory.daily_plan (plan_date DESC);


-- migrate:down

DROP TABLE IF EXISTS memory.daily_plan;
