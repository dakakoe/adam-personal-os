-- migrate:up

CREATE TABLE raw.telegram_user (
  source_user_id  BIGINT PRIMARY KEY,
  username        TEXT,
  first_name      TEXT,
  last_name       TEXT,
  phone           TEXT,
  is_bot          BOOLEAN NOT NULL DEFAULT false,
  first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload         JSONB NOT NULL
);

CREATE INDEX raw_telegram_user_phone_idx
  ON raw.telegram_user (phone)
  WHERE phone IS NOT NULL;

CREATE TABLE raw.telegram_chat (
  source_chat_id  BIGINT PRIMARY KEY,
  kind            TEXT NOT NULL,         -- 'private' | 'group' | 'supergroup' | 'channel'
  title           TEXT,
  username        TEXT,
  first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload         JSONB NOT NULL
);

CREATE TABLE raw.telegram_message (
  id                 BIGSERIAL PRIMARY KEY,
  chat_id            BIGINT NOT NULL,
  source_message_id  BIGINT NOT NULL,
  sender_id          BIGINT,
  message_date       TIMESTAMPTZ NOT NULL,
  ingested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  kind               TEXT NOT NULL,      -- 'text' | 'voice' | 'photo' | 'document' | 'sticker' | 'other'
  text               TEXT,
  voice_file_path    TEXT,
  payload            JSONB NOT NULL,
  CONSTRAINT raw_telegram_message_unique UNIQUE (chat_id, source_message_id)
);

CREATE INDEX raw_telegram_message_sender_date_idx
  ON raw.telegram_message (sender_id, message_date DESC)
  WHERE sender_id IS NOT NULL;

CREATE INDEX raw_telegram_message_ingested_idx
  ON raw.telegram_message (ingested_at);

CREATE INDEX raw_telegram_message_kind_idx
  ON raw.telegram_message (kind);

-- migrate:down

DROP TABLE IF EXISTS raw.telegram_message;
DROP TABLE IF EXISTS raw.telegram_chat;
DROP TABLE IF EXISTS raw.telegram_user;
