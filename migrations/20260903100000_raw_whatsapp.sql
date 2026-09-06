-- migrate:up

-- WhatsApp ingestion, raw layer.
--
-- Mirrors raw.telegram_* in spirit — append-only, one table per source shape,
-- the provider's own object kept whole in payload — with one structural
-- divergence that shapes everything below: a WhatsApp identifier is a TEXT
-- JID, not a BIGINT. There is no numeric user id to key on.
--
-- Two writers fill these tables and must agree:
--   * the live Baileys bridge (fetchers/whatsapp), linked as a companion
--     device, which sees new messages but almost no history;
--   * the one-shot iPhone-backup importer (fetchers/whatsapp_import), which
--     sees the full archive but is a snapshot.
-- Everything about the key choices below follows from making those two
-- converge without coordination.

-- ---------------------------------------------------------------------------
-- Contact directory
-- ---------------------------------------------------------------------------
-- Keyed on the NORMALIZED jid: device suffixes ('…:12@s.whatsapp.net') are
-- stripped before we ever store one, because the same human appears with a
-- different suffix per linked device.
--
-- Two name fields on purpose, and they are not interchangeable. notify_name is
-- the address-book name from your own phone (importer only) and is the good
-- one. push_name is what the contact chose to call themselves, which for
-- anyone who isn't a friend is frequently a shop name or an emoji.
CREATE TABLE raw.whatsapp_contact (
  jid            TEXT PRIMARY KEY,
  phone_e164     TEXT,                    -- bare international digits, no '+'
  lid            TEXT,                    -- '<n>@lid' alternate, when known
  push_name      TEXT,                    -- self-declared; low trust
  notify_name    TEXT,                    -- address-book; high trust
  business_name  TEXT,
  is_me          BOOLEAN NOT NULL DEFAULT false,
  first_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX raw_whatsapp_contact_phone_idx
  ON raw.whatsapp_contact (phone_e164) WHERE phone_e164 IS NOT NULL;
CREATE INDEX raw_whatsapp_contact_lid_idx
  ON raw.whatsapp_contact (lid) WHERE lid IS NOT NULL;

-- ---------------------------------------------------------------------------
-- LID -> phone-JID mapping
-- ---------------------------------------------------------------------------
-- Newer WhatsApp hides phone numbers behind "LIDs" ('<n>@lid') for people who
-- aren't in your contacts, so the live socket may only ever learn a LID for
-- someone the iPhone backup knows by phone number. Left unhandled that splits
-- one human into two people who — under the never-auto-merge rule — would stay
-- split forever.
--
-- So we record every LID<->phone pairing we observe (Baileys' alt fields,
-- contact upserts, group metadata) and let the normalizer upgrade a 'lid:N'
-- identity to its digit form later. This table is the only reason that
-- upgrade is possible after the fact.
CREATE TABLE raw.whatsapp_lid_map (
  lid         TEXT PRIMARY KEY,
  phone_jid   TEXT NOT NULL,
  first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX raw_whatsapp_lid_map_phone_idx ON raw.whatsapp_lid_map (phone_jid);

-- ---------------------------------------------------------------------------
-- Chat directory
-- ---------------------------------------------------------------------------
-- chat_key is the derived, stable handle for a conversation, and the thing
-- the normalizer joins on:
--   group           -> the full '…@g.us' jid (no LID duality for groups)
--   1:1 phone chat  -> bare international digits ('3725551234')
--   1:1 LID-only    -> 'lid:<n>'
-- chat_jid keeps whatever we actually saw, so nothing is lost.
CREATE TABLE raw.whatsapp_chat (
  chat_jid    TEXT PRIMARY KEY,
  chat_key    TEXT NOT NULL,
  kind        TEXT NOT NULL,              -- 'private' | 'group' | 'broadcast' | 'newsletter'
  title       TEXT,
  owner_jid   TEXT,
  first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload     JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX raw_whatsapp_chat_key_idx ON raw.whatsapp_chat (chat_key);

-- ---------------------------------------------------------------------------
-- Messages
-- ---------------------------------------------------------------------------
-- THE DEDUP KEY IS (source_message_id, from_me), NOT (chat, id).
--
-- This deliberately breaks parity with raw.telegram_message, which keys on
-- (chat_id, source_message_id). Two reasons, the second decisive:
--
--   1. WhatsApp's key.id is a client-generated random (128 bits on modern
--      clients), not a per-chat counter like Telegram's. It is already
--      effectively globally unique. from_me covers the one case where the same
--      id legitimately appears twice — the self-chat / multi-device echo,
--      where both copies are kept.
--
--   2. A chat-scoped key would make idempotency depend on the live bridge and
--      the iPhone importer deriving chat_key identically, and because of the
--      LID/phone duality above they demonstrably cannot: the importer only
--      ever sees phone jids, the socket may only have a LID. Keying on the
--      message id alone makes chat_jid/chat_key ordinary attributes we can
--      repair at leisure without ever touching the dedup key.
--
-- The cost: a genuine id collision across two different chats would be
-- silently dropped. At personal-corpus scale that probability is negligible,
-- and for read-only ingest the failure mode is one missing message rather than
-- a corrupted one.
CREATE TABLE raw.whatsapp_message (
  id                 BIGSERIAL PRIMARY KEY,
  chat_jid           TEXT NOT NULL,
  chat_key           TEXT NOT NULL,
  source_message_id  TEXT NOT NULL,       -- Baileys key.id == iOS ZWAMESSAGE.ZSTANZAID
  from_me            BOOLEAN NOT NULL,
  sender_jid         TEXT,                -- key.participant in groups; counterparty in DMs
  sender_phone_e164  TEXT,
  message_date       TIMESTAMPTZ NOT NULL,
  ingested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  origin             TEXT NOT NULL DEFAULT 'live',   -- 'live' | 'iphone_backup'
  kind               TEXT NOT NULL,       -- text|voice|audio|image|video|document|sticker|location|contact_card|other
  text               TEXT,
  -- Absolute path, and the column IS the contract: unlike the Telegram voice
  -- path, nothing downstream may reconstruct this from parts. WhatsApp chat
  -- keys and group jids contain characters that make reconstruction fragile.
  voice_file_path    TEXT,
  media_file_path    TEXT,
  quoted_message_id  TEXT,
  -- WhatsApp delivers @-mentions structured, so the normalizer needs no regex
  -- the way the Telegram group-mention extractor does.
  mentioned_jids     TEXT[],
  payload            JSONB NOT NULL,
  CONSTRAINT raw_whatsapp_message_unique UNIQUE (source_message_id, from_me)
);

-- Per-chat reads (group pages, group context on the person page) still need to
-- be fast even though the chat isn't part of the unique key.
CREATE INDEX raw_whatsapp_message_chat_date_idx
  ON raw.whatsapp_message (chat_key, message_date DESC);
CREATE INDEX raw_whatsapp_message_sender_date_idx
  ON raw.whatsapp_message (sender_jid, message_date DESC) WHERE sender_jid IS NOT NULL;
CREATE INDEX raw_whatsapp_message_ingested_idx ON raw.whatsapp_message (ingested_at);
CREATE INDEX raw_whatsapp_message_kind_idx ON raw.whatsapp_message (kind);

-- ---------------------------------------------------------------------------
-- Group opt-in
-- ---------------------------------------------------------------------------
-- Same contract as raw.telegram_group_allowlist: without a row, or with
-- enabled=FALSE, both writers see the chat and ignore its messages. Groups
-- self-populate on first sighting so the list fills in on its own, and the
-- importer applies the identical rule so history and live never disagree about
-- which groups are in the corpus.
CREATE TABLE raw.whatsapp_group_allowlist (
  chat_jid          TEXT PRIMARY KEY,
  title             TEXT,
  kind              TEXT NOT NULL DEFAULT 'group',
  member_count      INT,
  enabled           BOOLEAN NOT NULL DEFAULT FALSE,
  first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  enabled_at        TIMESTAMPTZ,
  last_message_at   TIMESTAMPTZ,
  dismissed_at      TIMESTAMPTZ
);

CREATE INDEX whatsapp_group_allowlist_enabled_idx
  ON raw.whatsapp_group_allowlist (chat_jid) WHERE enabled = TRUE;
CREATE INDEX whatsapp_group_allowlist_last_message_idx
  ON raw.whatsapp_group_allowlist (last_message_at DESC NULLS LAST);

-- migrate:down

DROP TABLE IF EXISTS raw.whatsapp_message;
DROP TABLE IF EXISTS raw.whatsapp_group_allowlist;
DROP TABLE IF EXISTS raw.whatsapp_chat;
DROP TABLE IF EXISTS raw.whatsapp_lid_map;
DROP TABLE IF EXISTS raw.whatsapp_contact;
