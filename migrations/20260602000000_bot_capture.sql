-- migrate:up

-- Pending captures from the Telegram bot (@yourbot): a text/voice note is
-- LLM-classified into a task / opportunity / calendar event and parked here as
-- 'pending' until the owner taps ✅ Confirm in Telegram. The parsed payload lives
-- in `parsed` (jsonb) so the bot's inline-button callback_data only has to
-- carry this row's id (Telegram caps callback_data at 64 bytes). Nothing is
-- created in memory.task / opportunity / Google Calendar until confirm.
CREATE TABLE memory.bot_capture (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source           TEXT NOT NULL,                       -- 'telegram_text' | 'telegram_voice'
  raw_text         TEXT NOT NULL,                       -- transcript or message text
  parsed           JSONB NOT NULL DEFAULT '{}'::jsonb,  -- classify_capture() output (resolved ids folded in)
  status           TEXT NOT NULL DEFAULT 'pending',     -- 'pending' | 'confirmed' | 'discarded'
  result_kind      TEXT,                                -- 'task' | 'opportunity' | 'event'
  result_id        UUID,                                -- task/opp id; NULL for calendar (external)
  result_ref       TEXT,                                -- gcal event id / htmlLink for events
  chat_id          BIGINT,                              -- Telegram chat the reply lives in
  reply_message_id BIGINT,                              -- message we edit on decision
  decided_at       TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT bot_capture_status_check CHECK (status IN ('pending','confirmed','discarded'))
);

CREATE INDEX bot_capture_status_idx ON memory.bot_capture (status, created_at DESC);

-- migrate:down

DROP TABLE IF EXISTS memory.bot_capture;
