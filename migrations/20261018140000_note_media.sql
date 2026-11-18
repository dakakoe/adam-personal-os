-- migrate:up

-- Images embedded in notes. The bytes live on disk under the notes media
-- dir (MERGE_API_NOTES_MEDIA_DIR); this row is the index: what it is, how
-- big, which page it was added to, and a content hash so the same file
-- pasted twice is stored once.
CREATE TABLE memory.note_media (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  note_id       UUID REFERENCES memory.note(id) ON DELETE SET NULL,
  kind          TEXT NOT NULL DEFAULT 'image',
  mime          TEXT NOT NULL,
  ext           TEXT NOT NULL,
  bytes         INT  NOT NULL,
  width         INT,
  height        INT,
  sha256        TEXT NOT NULL,
  original_name TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX note_media_sha_idx  ON memory.note_media (sha256);
CREATE INDEX note_media_note_idx ON memory.note_media (note_id) WHERE note_id IS NOT NULL;

-- Link preview cache: one OpenGraph fetch per URL per week. Failures are
-- cached too (status/error) so a dead link is not re-fetched on every open.
CREATE TABLE memory.link_preview (
  url          TEXT PRIMARY KEY,
  title        TEXT,
  description  TEXT,
  image_url    TEXT,
  site_name    TEXT,
  status       INT,
  error        TEXT,
  fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down

DROP TABLE IF EXISTS memory.link_preview;
DROP TABLE IF EXISTS memory.note_media;
