-- migrate:up

-- Phase 1 of the merge-as-personal-OS expansion: project / task /
-- opportunity entities, each soft-deletable and connected to the
-- existing canonical.person graph.
--
-- All three follow the same conventions as canonical.person and
-- memory.person_photo: gen_random_uuid PKs, soft delete via
-- deleted_at, touch updated_at trigger, partial index on the
-- common live-filter predicate.

-- --- project ---------------------------------------------------------

CREATE TABLE memory.project (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug            TEXT UNIQUE NOT NULL,        -- 'acme-app', 'personal', …
  name            TEXT NOT NULL,
  description     TEXT,
  -- 'active' | 'paused' | 'archived' — enforced by check
  status          TEXT NOT NULL DEFAULT 'active',
  -- Hex color for UI badging (e.g. '#7C3AED' for a project's brand)
  color           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ,
  CONSTRAINT project_status_check CHECK (status IN ('active','paused','archived'))
);

CREATE TRIGGER memory_project_touch_updated_at
  BEFORE UPDATE ON memory.project
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

CREATE INDEX memory_project_status_idx ON memory.project (status)
 WHERE deleted_at IS NULL;

-- Project ↔ person membership. role is free text (CEO, employee,
-- partner, contractor, etc.) — leaning on flexibility over a fixed
-- enum since the cast varies wildly across your
-- own projects.
CREATE TABLE memory.project_member (
  project_id      UUID NOT NULL REFERENCES memory.project(id) ON DELETE CASCADE,
  person_id       UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  role            TEXT,
  added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, person_id)
);

CREATE INDEX memory_project_member_person_idx
  ON memory.project_member (person_id);


-- --- opportunity ----------------------------------------------------

-- Stages chosen to match the owner's actual deal pattern across
-- their own projects (not the generic CRM lead/qualified/…).
-- 'lost' is a terminal off-ramp; 'active' is the steady-state win.
CREATE TYPE memory.opportunity_stage AS ENUM
  ('intro', 'conversations', 'mou', 'contract', 'active', 'lost');

CREATE TABLE memory.opportunity (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title             TEXT NOT NULL,
  description       TEXT,
  project_id        UUID REFERENCES memory.project(id) ON DELETE SET NULL,
  -- Counterparty: the person this deal is *with* (lead, partner, target).
  counterparty_id   UUID REFERENCES canonical.person(id) ON DELETE SET NULL,
  stage             memory.opportunity_stage NOT NULL DEFAULT 'intro',
  -- Free-text so it can hold "$12k/mo", "MoU signature", "intro to KBW",
  -- "TBD" — not just numeric value.
  estimated_value   TEXT,
  -- Forward-looking provenance: which Phase 2/3 source surfaced this
  source_kind       TEXT,                       -- 'manual'|'granola'|'interaction'|...
  source_ref        TEXT,                       -- transcript id / interaction uuid
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at         TIMESTAMPTZ,
  deleted_at        TIMESTAMPTZ
);

CREATE TRIGGER memory_opportunity_touch_updated_at
  BEFORE UPDATE ON memory.opportunity
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

CREATE INDEX memory_opportunity_stage_idx ON memory.opportunity (stage)
 WHERE deleted_at IS NULL;
CREATE INDEX memory_opportunity_counterparty_idx
  ON memory.opportunity (counterparty_id) WHERE counterparty_id IS NOT NULL;
CREATE INDEX memory_opportunity_project_idx
  ON memory.opportunity (project_id) WHERE project_id IS NOT NULL;


-- --- task -----------------------------------------------------------

CREATE TABLE memory.task (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title           TEXT NOT NULL,
  description     TEXT,
  project_id      UUID REFERENCES memory.project(id) ON DELETE SET NULL,
  -- A task can live under an opportunity (a step toward closing it),
  -- under a project standalone, or be person-relational only.
  opportunity_id  UUID REFERENCES memory.opportunity(id) ON DELETE SET NULL,
  -- The contact this task is *with*. Assignee is always the owner (the
  -- single-user case); we don't model assignment explicitly.
  with_person_id  UUID REFERENCES canonical.person(id) ON DELETE SET NULL,
  status          TEXT NOT NULL DEFAULT 'open',
  due_date        DATE,
  -- Provenance same as opportunity
  source_kind     TEXT,
  source_ref      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at    TIMESTAMPTZ,
  deleted_at      TIMESTAMPTZ,
  CONSTRAINT task_status_check CHECK (status IN ('open','doing','done','cancelled'))
);

CREATE TRIGGER memory_task_touch_updated_at
  BEFORE UPDATE ON memory.task
  FOR EACH ROW EXECUTE FUNCTION public._touch_updated_at();

CREATE INDEX memory_task_status_due_idx
  ON memory.task (status, due_date NULLS LAST) WHERE deleted_at IS NULL;
CREATE INDEX memory_task_project_idx
  ON memory.task (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX memory_task_with_person_idx
  ON memory.task (with_person_id) WHERE with_person_id IS NOT NULL;
CREATE INDEX memory_task_opportunity_idx
  ON memory.task (opportunity_id) WHERE opportunity_id IS NOT NULL;


-- --- seed: example projects + 1 task (fictional demo data) ------------

INSERT INTO memory.project (slug, name, description, color) VALUES
  ('acme-app', 'Acme App',
   'Example project. Replace with your own — or delete this seed block.',
   '#7C3AED'),
  ('personal', 'Personal',
   'Example project for personal to-dos.',
   '#10B981');

INSERT INTO memory.task (title, description, project_id, status, due_date, source_kind, source_ref)
SELECT
  'Set up your first project',
  'Example task seeded on install — edit or delete it.',
  p.id, 'open', CURRENT_DATE, 'seed', 'example-1'
FROM memory.project p WHERE p.slug = 'acme-app';

-- migrate:down

DROP TABLE IF EXISTS memory.task;
DROP TABLE IF EXISTS memory.opportunity;
DROP TYPE  IF EXISTS memory.opportunity_stage;
DROP TABLE IF EXISTS memory.project_member;
DROP TABLE IF EXISTS memory.project;
