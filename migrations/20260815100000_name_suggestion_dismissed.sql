-- migrate:up

-- Names you've already said no to.
--
-- The suggestion banner reads candidate names straight out of the raw tables
-- (Telegram profiles, LinkedIn exports, Google Contacts), so it has no memory:
-- every page load re-derives the same list and offers it again. Applying one
-- is recorded — it becomes the display name — but declining one wasn't
-- recorded anywhere, so there was no way to decline.
--
-- That's most obvious after a merge, where a contact ends up carrying two
-- Telegram profiles and the banner offers both of the other account's names
-- forever.
--
-- Stored per person rather than globally: the same name can be right for one
-- contact and wrong for another.
CREATE TABLE memory.name_suggestion_dismissed (
  id          BIGSERIAL PRIMARY KEY,
  person_id   UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Plain (not lower()) so execute_merge's dedup step can express it as
  -- column equality. Case variants would just make two rows, which costs
  -- nothing — the suggestion filter compares case-insensitively anyway.
  CONSTRAINT name_suggestion_dismissed_unique UNIQUE (person_id, name)
);

-- migrate:down

DROP TABLE IF EXISTS memory.name_suggestion_dismissed;
