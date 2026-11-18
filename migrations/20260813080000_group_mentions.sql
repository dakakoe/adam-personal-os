-- migrate:up

-- Reaching out to someone in a GROUP chat.
--
-- Group messages are ingested (40k of them, from the 82 allow-listed groups)
-- but land in canonical.interaction with person_id NULL: normalizer/sync.py
-- maps the CHAT to a person, which is right for a private dialog — where the
-- chat *is* the other person — and impossible for a group. So "I said hi to
-- someone in the founders group today" was invisible, and they kept showing as
-- four months overdue.
--
-- This records the one thing that unambiguously means you addressed someone:
-- an outbound message of yours that @-mentions their handle. Deliberately NOT
-- written into canonical.interaction — that would change interaction counts,
-- last-contact and prospect ranking across the whole app. This table feeds
-- exactly two questions: has a follow-up been settled, and is a circle cadence
-- satisfied.
--
-- Not counted, on purpose: merely posting in a group you're both in. That's
-- co-presence, not contact, and treating it as contact would quietly empty the
-- follow-up list.
CREATE TABLE memory.group_mention (
  id           BIGSERIAL PRIMARY KEY,
  person_id    UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  raw_id       BIGINT NOT NULL,        -- raw.telegram_message.id it came from
  chat_id      BIGINT NOT NULL,        -- negative: a group/channel
  handle       TEXT   NOT NULL,        -- the @handle as matched, lowercased
  occurred_at  TIMESTAMPTZ NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- One row per person per message: a message can name several people, and
  -- re-running the extractor must not duplicate.
  CONSTRAINT group_mention_unique UNIQUE (person_id, raw_id)
);

CREATE INDEX memory_group_mention_person_idx
  ON memory.group_mention (person_id, occurred_at DESC);

-- migrate:down

DROP TABLE IF EXISTS memory.group_mention;
