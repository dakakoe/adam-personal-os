-- migrate:up

-- Per-group opt-in for Telegram ingestion. Without a row here OR with
-- enabled=FALSE, the live + backfill fetchers see the chat but ignore
-- its messages. The fetchers themselves auto-discover (insert with
-- enabled=FALSE on first sighting) so this table self-populates as
-- new groups appear in the user's dialog list. A separate UI flips
-- enabled=TRUE for chats the user actually wants in their corpus.
--
-- chat_id is the Telegram peer ID; negative for groups, ~`-100…` for
-- supergroups, positive for channels in some flows. We store the raw
-- value Telethon hands us (msg.chat_id, dialog.entity.id forms) so
-- there's no ambiguity at lookup time.
CREATE TABLE raw.telegram_group_allowlist (
  chat_id           BIGINT PRIMARY KEY,
  title             TEXT,
  kind              TEXT NOT NULL,                 -- 'group' | 'supergroup' | 'channel'
  member_count      INT,
  enabled           BOOLEAN NOT NULL DEFAULT FALSE,
  first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  enabled_at        TIMESTAMPTZ                    -- when the user opted in
);

-- Fast filter for the live ingest's hot-path lookup ("is this chat enabled?")
CREATE INDEX telegram_group_allowlist_enabled_idx
  ON raw.telegram_group_allowlist (chat_id)
 WHERE enabled = TRUE;

-- migrate:down

DROP TABLE IF EXISTS raw.telegram_group_allowlist;
