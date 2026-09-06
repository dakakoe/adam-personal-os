-- migrate:up

-- Extend the per-sender cleanup preference with a "clear list": senders the user
-- flags (e.g. from the message view while reading junk) to bulk-trash later. A
-- sender is at most one of keep / clear (the API enforces mutual exclusion); a
-- row with both false is deleted.
ALTER TABLE memory.mail_sender_pref
  ADD COLUMN IF NOT EXISTS clear BOOLEAN NOT NULL DEFAULT false;

-- migrate:down

ALTER TABLE memory.mail_sender_pref DROP COLUMN IF EXISTS clear;
