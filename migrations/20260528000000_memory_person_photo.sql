-- migrate:up

-- One photo per person, populated by the avatar backfillers. The endpoint
-- /api/persons/{id}/photo prefers higher-quality / longer-lived sources
-- (local Telegram download → Google Contacts CDN URL → Gravatar fallback,
-- the last computed on-the-fly from any email identity and not stored
-- here). The table holds whichever non-Gravatar source actually populated.
CREATE TABLE memory.person_photo (
  person_id   UUID PRIMARY KEY REFERENCES canonical.person(id) ON DELETE CASCADE,
  source      TEXT NOT NULL,           -- 'telegram' | 'google_contacts'
  url         TEXT,                    -- public CDN URL (google_contacts → lh3.googleusercontent.com)
  local_path  TEXT,                    -- /srv/memory/data/avatars/<file>.jpg (telegram downloads)
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT person_photo_url_or_path CHECK (url IS NOT NULL OR local_path IS NOT NULL),
  CONSTRAINT person_photo_source_check CHECK (source IN ('telegram','google_contacts'))
);

-- migrate:down

DROP TABLE IF EXISTS memory.person_photo;
