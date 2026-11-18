-- migrate:up

-- Tracks the Twenty CRM Person.id that mirrors each canonical.person, so the
-- sync job is idempotent (re-runs UPDATE by twenty_id instead of trying to
-- match on display_name and risking duplicates). Nullable until first sync.
ALTER TABLE canonical.person
    ADD COLUMN twenty_id TEXT UNIQUE;

-- migrate:down

ALTER TABLE canonical.person DROP COLUMN twenty_id;
