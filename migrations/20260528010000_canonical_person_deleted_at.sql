-- migrate:up

-- Soft delete for canonical.person. Same pattern as merged_into: a
-- nullable timestamp; all live queries filter `deleted_at IS NULL`
-- alongside `merged_into IS NULL`. Soft instead of hard so the user
-- can restore from a too-aggressive cleanup sweep (e.g. realized a
-- noreply@ sender was actually a real human contact).
--
-- Interactions stay attached via FK ON DELETE SET NULL anyway, but
-- soft delete keeps the row + identities + summary + photo intact so
-- restore is a single UPDATE rather than recreating from scratch.
ALTER TABLE canonical.person
  ADD COLUMN deleted_at TIMESTAMPTZ;

-- Partial index for the cleanup queue (list deleted-but-not-yet-purged)
-- and to keep the common filter `WHERE deleted_at IS NULL` cheap when
-- the deleted set grows.
CREATE INDEX canonical_person_deleted_at_idx
  ON canonical.person (deleted_at)
 WHERE deleted_at IS NOT NULL;

-- migrate:down

DROP INDEX IF EXISTS canonical.canonical_person_deleted_at_idx;
ALTER TABLE canonical.person DROP COLUMN IF EXISTS deleted_at;
