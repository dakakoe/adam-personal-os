-- migrate:up

-- Who WROTE a message, as distinct from who it was with.
--
-- 42,336 group messages sit in canonical.interaction with person_id NULL.
-- They are embedded and searchable, so local_ask retrieves exactly the right
-- message and then reports its author as "(unknown)" — the topic without the
-- name, which is the wrong half for "who can help me with X".
--
-- The obvious fix, setting person_id, is the one thing we must NOT do. The
-- group_mention migration spells out why: "merely posting in a group you're
-- both in ... is co-presence, not contact, and treating it as contact would
-- quietly empty the follow-up list." person_id means "this was contact with
-- X" and is what follow-ups, cadences, interaction counts and prospect
-- ranking all read. Overloading it would silently rewrite all of them.
--
-- So authorship gets its own column. person_id keeps its exact meaning and
-- every existing query is untouched; retrieval gains a name it did not have.
--
-- Set only where person_id IS NULL — i.e. group messages. In a 1:1 the author
-- is already implied by person_id and direction, and duplicating it there
-- would invite exactly the confusion this column exists to avoid.
ALTER TABLE canonical.interaction
  ADD COLUMN IF NOT EXISTS author_person_id UUID
    REFERENCES canonical.person(id) ON DELETE SET NULL;

COMMENT ON COLUMN canonical.interaction.author_person_id IS
  'Who wrote this, for rows with no 1:1 counterparty (group messages). '
  'Attribution only — never contact. Follow-ups, cadences and interaction '
  'counts read person_id and must continue to ignore this column.';

-- Retrieval joins on it to name a group message's author, and the profile
-- builder pulls a person's own group messages through it.
CREATE INDEX IF NOT EXISTS interaction_author_idx
  ON canonical.interaction (author_person_id, occurred_at DESC)
  WHERE author_person_id IS NOT NULL;

-- migrate:down

DROP INDEX IF EXISTS canonical.interaction_author_idx;
ALTER TABLE canonical.interaction DROP COLUMN IF EXISTS author_person_id;
