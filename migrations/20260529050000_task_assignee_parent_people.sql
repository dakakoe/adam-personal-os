-- migrate:up

-- Task card redesign (Phase 5a): richer tasks.
--   - assignee_person_id: who owns the task. NULL = "Me" (the owner, the
--     single user). Non-null = delegated to a contact.
--   - parent_task_id: a subtask is just a task with a parent. Self-
--     referential, ON DELETE CASCADE so deleting a parent removes its
--     subtasks. Top-level tasks have parent_task_id IS NULL.
--   - memory.task_person: the "people involved" set (many-to-many). This
--     supersedes the single with_person_id; we seed it from the existing
--     with_person_id values and keep that column as the legacy "primary
--     contact" pointer (still set by suggestion-accept).

ALTER TABLE memory.task
  ADD COLUMN assignee_person_id UUID REFERENCES canonical.person(id) ON DELETE SET NULL,
  ADD COLUMN parent_task_id     UUID REFERENCES memory.task(id) ON DELETE CASCADE;

CREATE INDEX memory_task_parent_idx
  ON memory.task (parent_task_id) WHERE parent_task_id IS NOT NULL;
CREATE INDEX memory_task_assignee_idx
  ON memory.task (assignee_person_id) WHERE assignee_person_id IS NOT NULL;

CREATE TABLE memory.task_person (
  task_id    UUID NOT NULL REFERENCES memory.task(id) ON DELETE CASCADE,
  person_id  UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (task_id, person_id)
);

CREATE INDEX memory_task_person_person_idx
  ON memory.task_person (person_id);

-- Seed "people involved" from the existing single contact.
INSERT INTO memory.task_person (task_id, person_id)
SELECT t.id, t.with_person_id
  FROM memory.task t
 WHERE t.with_person_id IS NOT NULL
   AND t.deleted_at IS NULL
ON CONFLICT DO NOTHING;

-- migrate:down

DROP TABLE IF EXISTS memory.task_person;
ALTER TABLE memory.task
  DROP COLUMN IF EXISTS parent_task_id,
  DROP COLUMN IF EXISTS assignee_person_id;
