-- migrate:up

-- Mail round 2 / P3 (senders cleanup view): per-sender lookups — newest message,
-- unsubscribe header probe, bulk thread-key collection for archive/read/trash —
-- all probe by from_address; without this they're a seq scan per sender.
CREATE INDEX IF NOT EXISTS gmail_message_from_idx
  ON raw.gmail_message (from_address, internal_date DESC);

-- migrate:down

DROP INDEX IF EXISTS raw.gmail_message_from_idx;
