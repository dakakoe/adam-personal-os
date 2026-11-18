-- migrate:up

-- A recurring routine: a TEMPLATE that materializes one concrete memory.task
-- per matching day (source_kind='recurring', source_ref=this id). Completing a
-- day's instance never touches the template; the next occurrence is generated
-- on schedule. This fits the daily-planner model — instances flow into
-- /today, /tasks, /plan like any task.
CREATE TABLE memory.recurring_task (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title           TEXT NOT NULL,
  description     TEXT,
  project_id      UUID REFERENCES memory.project(id) ON DELETE SET NULL,
  with_person_id  UUID REFERENCES canonical.person(id) ON DELETE SET NULL,
  -- null = an all-day reminder for the day; otherwise the time it's due.
  at_time         TIME,
  -- 'daily' | 'weekly' | 'monthly' | 'yearly'
  freq            TEXT NOT NULL,
  -- weekly only: ISO weekday indices 0=Mon … 6=Sun. Weekdays = {0,1,2,3,4}.
  byweekday       SMALLINT[] NOT NULL DEFAULT '{}',
  -- start of the routine; for monthly/yearly it also encodes the day-of-month
  -- (and month, for yearly) it recurs on.
  anchor_date     DATE NOT NULL,
  active          BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ,
  CONSTRAINT recurring_task_freq_check CHECK (freq IN ('daily','weekly','monthly','yearly'))
);

CREATE INDEX recurring_task_active_idx ON memory.recurring_task (active) WHERE deleted_at IS NULL;

CREATE TRIGGER recurring_task_touch BEFORE UPDATE ON memory.recurring_task
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

-- Optional time-of-day on a task (null = all-day). Lets a timed routine's
-- instance carry and display its time; also useful for any task.
ALTER TABLE memory.task ADD COLUMN IF NOT EXISTS due_time TIME;

-- One generated instance per (template, day) — ever. Makes generation
-- idempotent (the daily timer + generate-on-save can run freely) AND means a
-- deliberately-deleted instance is not resurrected later that same day.
CREATE UNIQUE INDEX IF NOT EXISTS task_recurring_per_day_idx
  ON memory.task (source_ref, due_date)
  WHERE source_kind = 'recurring';

-- migrate:down

DROP INDEX IF EXISTS memory.task_recurring_per_day_idx;
ALTER TABLE memory.task DROP COLUMN IF EXISTS due_time;
DROP TABLE IF EXISTS memory.recurring_task;
