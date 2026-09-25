-- migrate:up

-- Outbox for sending a reviewed draft as a Telegram message FROM the owner's own
-- account. The merge API can't touch the Telethon session (the always-on
-- memory-telethon process holds it), so a "Send" enqueues here and the live
-- Telethon process — which already has the session + the contact's cached
-- entity — picks it up and sends. NOTHING lands here without an explicit user
-- "Send via Telegram" action on a draft.
CREATE TABLE memory.telegram_outbox (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  person_id   UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  draft_id    UUID REFERENCES memory.draft(id) ON DELETE SET NULL,
  tg_user_id  BIGINT NOT NULL,                    -- resolved at enqueue from the telegram identity
  body        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',    -- 'pending' | 'sent' | 'failed'
  error       TEXT,
  attempts    INT NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at     TIMESTAMPTZ,
  CONSTRAINT telegram_outbox_status_check CHECK (status IN ('pending','sent','failed'))
);

CREATE INDEX telegram_outbox_pending_idx
  ON memory.telegram_outbox (created_at) WHERE status = 'pending';

-- migrate:down

DROP TABLE IF EXISTS memory.telegram_outbox;
