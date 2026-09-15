-- migrate:up

-- Mail UX: an app-local "read" overlay on the existing per-thread state, alongside
-- archived/starred. A thread shows as unread iff Gmail still has UNREAD AND it isn't
-- locally marked read — so "Mark all read" and read-on-open work without touching
-- Gmail (like archive/star did before the Phase 3c two-way push).
ALTER TABLE memory.mail_state ADD COLUMN IF NOT EXISTS read BOOLEAN NOT NULL DEFAULT false;

-- migrate:down

ALTER TABLE memory.mail_state DROP COLUMN IF EXISTS read;
