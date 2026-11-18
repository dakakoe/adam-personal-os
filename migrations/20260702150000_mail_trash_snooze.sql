-- migrate:up
-- Mail polish: trash (maps to Gmail TRASH — Google purges after ~30d, that IS
-- the delete) and snooze (LOCAL-ONLY: Gmail has no snooze label; parity is
-- deliberately not attempted).
ALTER TABLE memory.mail_state ADD COLUMN IF NOT EXISTS trashed BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE memory.mail_state ADD COLUMN IF NOT EXISTS snoozed_until TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS mail_state_trashed_idx ON memory.mail_state (trashed) WHERE trashed;
CREATE INDEX IF NOT EXISTS mail_state_snoozed_idx ON memory.mail_state (snoozed_until) WHERE snoozed_until IS NOT NULL;

-- migrate:down
DROP INDEX IF EXISTS memory.mail_state_snoozed_idx;
DROP INDEX IF EXISTS memory.mail_state_trashed_idx;
ALTER TABLE memory.mail_state DROP COLUMN IF EXISTS snoozed_until;
ALTER TABLE memory.mail_state DROP COLUMN IF EXISTS trashed;
