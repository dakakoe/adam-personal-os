-- migrate:up

-- Generic per-source health flag. Background workers (Telegram/Telethon,
-- Granola) set status='needs_attention' with a short reason when a source
-- needs human intervention — e.g. a revoked Telethon session or an invalid
-- Granola API key. /api/sources reads this to render a "needs attention"
-- badge on the Sources page.
--
-- This mirrors the role raw.gmail_account.status already plays for Gmail/GCal,
-- but generically for sources that have no status column of their own.
--
-- status is intentionally free-form (no CHECK), matching how the codebase
-- treats raw.gmail_account.status as free text. updated_at is writer-set
-- (workers upsert and stamp it), like memory.app_setting — no trigger.
CREATE TABLE memory.source_status (
  source_key  TEXT PRIMARY KEY,             -- 'telegram' | 'granola' (matches SOURCES keys in merge_api app.py)
  status      TEXT NOT NULL DEFAULT 'ok',   -- 'ok' | 'needs_attention'
  reason      TEXT,                         -- short human-readable reason, nullable
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down

DROP TABLE IF EXISTS memory.source_status;
