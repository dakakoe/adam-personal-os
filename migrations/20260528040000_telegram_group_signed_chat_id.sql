-- migrate:up

-- raw.telegram_group_allowlist was storing chat_id in the positive
-- `entity.id` form (what Telethon's iter_dialogs() returns), but
-- raw.telegram_message stores the SIGNED form (what `msg.chat_id`
-- returns). Convention:
--   legacy group   →  -N        (just negated)
--   supergroup     →  -100…N    (Telegram channel-namespace prefix)
--   channel        →  -100…N    (same as supergroup)
--   private (User) →  N         (positive, but private chats aren't here)
--
-- The mismatch meant JOIN raw.telegram_message m ON m.chat_id = g.chat_id
-- never matched a row, so the UI couldn't show how many messages had
-- been ingested per group. From now on, the upsert helper writes the
-- signed form (telethon.utils.get_peer_id(entity)). Existing 2,437
-- rows get rewritten below using their kind to pick the right formula.

-- For supergroups and channels: prepend the -100… channel prefix.
UPDATE raw.telegram_group_allowlist
   SET chat_id = -1000000000000 - chat_id
 WHERE chat_id > 0
   AND kind IN ('channel', 'supergroup');

-- For legacy groups: just negate.
UPDATE raw.telegram_group_allowlist
   SET chat_id = -chat_id
 WHERE chat_id > 0
   AND kind IN ('group', 'other');

-- migrate:down

-- Reverse: flip back to positive form using the same kind mapping.
UPDATE raw.telegram_group_allowlist
   SET chat_id = -1000000000000 - chat_id
 WHERE chat_id < 0
   AND kind IN ('channel', 'supergroup');

UPDATE raw.telegram_group_allowlist
   SET chat_id = -chat_id
 WHERE chat_id < 0
   AND kind IN ('group', 'other');
