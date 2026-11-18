-- migrate:up

-- Make memory.group_mention hold mentions from more than one source.
--
-- The table was written when Telegram was the only group-bearing channel, and
-- it bakes that in three ways that all break the moment raw.whatsapp_message
-- exists:
--
--   1. chat_id BIGINT NOT NULL — a Telegram peer id. A WhatsApp group is a
--      text jid ('120363…@g.us') and does not fit.
--
--   2. UNIQUE (person_id, raw_id), where raw_id is an unFK'd pointer into
--      raw.telegram_message. raw.whatsapp_message has its own independent
--      BIGSERIAL, so the two id spaces overlap from row one. A WhatsApp
--      mention of person P in whatsapp row 42 and a Telegram mention of P in
--      telegram row 42 are indistinguishable, and the second is swallowed by
--      the extractor's ON CONFLICT DO NOTHING. That is silent data loss, not
--      an error anyone would see.
--
--   3. _MERGE_REPOINTS in merge_api/merge_api/queries.py carries the dedup
--      tuple ("raw_id",) for this table, which tells execute_merge to DELETE
--      loser rows colliding on (person_id, raw_id). Post-WhatsApp that would
--      delete rows from the other source that it should have kept. That tuple
--      is updated to ("raw_source", "raw_id") in the same change as this
--      migration; the two must stay in step.
--
-- Fix: qualify every row by which raw table it came from, and carry the chat
-- as text alongside the legacy numeric column.
--
-- Every reader was checked (circle cadence, follow-up settle, the two
-- prospect/cadence queries, test_sql_aliases) and all of them select only
-- person_id and occurred_at, so widening the key is invisible to them.

ALTER TABLE memory.group_mention
  ADD COLUMN IF NOT EXISTS raw_source TEXT NOT NULL DEFAULT 'raw.telegram_message',
  ADD COLUMN IF NOT EXISTS chat_key   TEXT;

COMMENT ON COLUMN memory.group_mention.raw_source IS
  'Which raw table raw_id points into: raw.telegram_message | raw.whatsapp_message';
COMMENT ON COLUMN memory.group_mention.chat_key IS
  'Source-native group handle: Telegram peer id as text, or a WhatsApp @g.us jid';

-- Existing rows are all Telegram; their chat_key is the peer id as text.
UPDATE memory.group_mention SET chat_key = chat_id::text WHERE chat_key IS NULL;

-- chat_id stays for the Telegram rows that already use it, but can no longer
-- be required — WhatsApp has nothing numeric to put there.
ALTER TABLE memory.group_mention ALTER COLUMN chat_id DROP NOT NULL;

ALTER TABLE memory.group_mention DROP CONSTRAINT IF EXISTS group_mention_unique;
ALTER TABLE memory.group_mention
  ADD CONSTRAINT group_mention_unique UNIQUE (person_id, raw_source, raw_id);

-- migrate:down

-- Drop the non-Telegram rows first: the narrow key cannot represent them, and
-- restoring chat_id NOT NULL would fail on them anyway.
DELETE FROM memory.group_mention WHERE raw_source <> 'raw.telegram_message';

ALTER TABLE memory.group_mention DROP CONSTRAINT IF EXISTS group_mention_unique;
ALTER TABLE memory.group_mention
  ADD CONSTRAINT group_mention_unique UNIQUE (person_id, raw_id);

ALTER TABLE memory.group_mention ALTER COLUMN chat_id SET NOT NULL;
ALTER TABLE memory.group_mention DROP COLUMN IF EXISTS chat_key;
ALTER TABLE memory.group_mention DROP COLUMN IF EXISTS raw_source;
