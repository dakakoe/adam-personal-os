-- migrate:up

-- Phase 2 of the personal-OS expansion: meeting-recap ingestion +
-- a human-in-the-loop suggestion inbox.
--
-- Flow: a Granola meeting (recap + attendees) is POSTed to
-- /api/ingest/granola. We store the recap (memory.meeting_recap),
-- run a Haiku extraction over it, and write memory.suggestion rows
-- (action items → task suggestions, deals → opportunity suggestions,
-- DB-matched people → "create a task with X" suggestions). Nothing
-- becomes a real task/opportunity until the user accepts it from the
-- inbox — same approval-strict pattern as the merge queue.

-- --- meeting_recap ---------------------------------------------------

-- Source-agnostic name (meeting_recap, not granola_recap) so a future
-- Zoom/Otter/manual paste reuses the same table via `source`.
CREATE TABLE memory.meeting_recap (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source          TEXT NOT NULL DEFAULT 'granola',
  -- External id for idempotency. (source, source_id) unique so
  -- re-ingesting the same Granola meeting updates rather than dupes.
  source_id       TEXT NOT NULL,
  title           TEXT,
  meeting_date    TIMESTAMPTZ,
  -- Project this recap was attributed to (Haiku-inferred, user can
  -- re-point later). NULL when no confident match.
  project_id      UUID REFERENCES memory.project(id) ON DELETE SET NULL,
  -- The raw Granola AI summary we ingested (markdown).
  summary         TEXT,
  -- Haiku's condensed 1-3 sentence recap for quick scanning.
  recap           TEXT,
  -- Raw attendee blob from the source (names + emails).
  attendees       JSONB NOT NULL DEFAULT '[]'::jsonb,
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at    TIMESTAMPTZ,         -- when Haiku extraction completed
  CONSTRAINT meeting_recap_source_unique UNIQUE (source, source_id)
);

CREATE INDEX memory_meeting_recap_project_idx
  ON memory.meeting_recap (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX memory_meeting_recap_date_idx
  ON memory.meeting_recap (meeting_date DESC NULLS LAST);


-- --- suggestion (the inbox) -----------------------------------------

-- kind:
--   'task'           — an action item → becomes a memory.task on accept
--   'opportunity'    — a deal/partnership → memory.opportunity on accept
--   'person_mention' — a DB contact came up → "task with X" on accept
-- status: pending → accepted | dismissed
CREATE TABLE memory.suggestion (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind                TEXT NOT NULL,
  status              TEXT NOT NULL DEFAULT 'pending',
  title               TEXT NOT NULL,
  detail              TEXT,                  -- reasoning / context snippet
  -- Inferred linkages (any may be NULL):
  project_id          UUID REFERENCES memory.project(id) ON DELETE SET NULL,
  person_id           UUID REFERENCES canonical.person(id) ON DELETE SET NULL,
  -- The raw name Haiku saw, kept even when person_id resolves (audit)
  -- and as the only handle when no canonical.person matched.
  person_name_raw     TEXT,
  -- For kind='opportunity': proposed stage + value.
  suggested_stage     memory.opportunity_stage,
  estimated_value     TEXT,
  -- For kind='task': proposed due date + whose action it is.
  due_date            DATE,
  owner_hint          TEXT,                  -- 'the owner' | 'Member' | ... (informational)
  confidence          TEXT NOT NULL DEFAULT 'medium',  -- high|medium|low
  source_kind         TEXT NOT NULL DEFAULT 'granola',
  source_ref          TEXT,                  -- meeting_recap.id::text
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at          TIMESTAMPTZ,
  -- What it materialized into on accept, for traceability + undo.
  accepted_entity_kind TEXT,                 -- 'task' | 'opportunity'
  accepted_entity_id   UUID,
  CONSTRAINT suggestion_kind_check   CHECK (kind   IN ('task','opportunity','person_mention')),
  CONSTRAINT suggestion_status_check CHECK (status IN ('pending','accepted','dismissed'))
);

-- The inbox query: pending first, newest first, grouped by source.
CREATE INDEX memory_suggestion_status_idx
  ON memory.suggestion (status, created_at DESC);
CREATE INDEX memory_suggestion_source_ref_idx
  ON memory.suggestion (source_ref);
CREATE INDEX memory_suggestion_person_idx
  ON memory.suggestion (person_id) WHERE person_id IS NOT NULL;

-- migrate:down

DROP TABLE IF EXISTS memory.suggestion;
DROP TABLE IF EXISTS memory.meeting_recap;
