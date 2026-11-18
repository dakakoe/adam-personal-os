-- migrate:up

-- How much a follow-up matters, so the backlog can be worked in the order you
-- actually care about rather than purely by how late it is.
--
-- Three levels and no more: the point is to sort a wall of overdue
-- conversations into "these first", "these eventually" and everything else.
-- A numeric score would invite fiddling without changing what you do next.
--
-- 'mid' is the default because an unlabelled follow-up is an ordinary one —
-- every row that exists today means exactly what it meant before this column.
-- Stored as text rather than an int so the value reads the same in the API,
-- the UI and psql; the ordering lives in one CASE (FOLLOWUP_PRIORITY_RANK).
ALTER TABLE memory.followup
  ADD COLUMN priority TEXT NOT NULL DEFAULT 'mid'
    CONSTRAINT followup_priority_check CHECK (priority IN ('low', 'mid', 'high'));

-- migrate:down

ALTER TABLE memory.followup DROP COLUMN IF EXISTS priority;
