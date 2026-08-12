-- migrate:up
-- Setup wizard: per-source secrets settable from the UI (v1: the Granola API
-- key). Stored in Postgres rather than /srv/memory/secrets/.env so the API
-- never writes secrets files and the key can be rotated from the wizard.
-- At-rest exposure = the same class as raw.gmail_account.refresh_token (both
-- land in DB backups) — no new exposure class. Env vars stay as the fallback
-- and bootstrap path; a DB row wins over env so UI rotation takes effect.
CREATE TABLE memory.source_secret (
    source_key TEXT PRIMARY KEY,
    secret     TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS memory.source_secret;
