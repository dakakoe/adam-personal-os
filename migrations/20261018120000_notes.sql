-- migrate:up

-- Notes: self-hosted pages for ideas, Notion-style. One table, a tree via
-- parent_id, and two copies of the content:
--   doc      the editor's own document (ProseMirror JSON) — what the UI edits
--   body_md  a markdown mirror — what search indexes and what the assistant
--            connector reads and writes. When the assistant writes, doc is
--            cleared and the editor rebuilds it from the markdown on open.
-- Pages can hang off a person, a deal or a project so they show up in context.
CREATE TABLE memory.note (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title           TEXT NOT NULL DEFAULT '',
  parent_id       UUID REFERENCES memory.note(id) ON DELETE SET NULL,
  doc             JSONB,
  body_md         TEXT NOT NULL DEFAULT '',
  person_id       UUID REFERENCES canonical.person(id) ON DELETE SET NULL,
  opportunity_id  UUID REFERENCES memory.opportunity(id) ON DELETE SET NULL,
  project_id      UUID REFERENCES memory.project(id) ON DELETE SET NULL,
  pinned          BOOLEAN NOT NULL DEFAULT false,
  sort            INT NOT NULL DEFAULT 0,
  -- 'simple' on purpose: notes mix Russian and English, and a language
  -- stemmer would mangle whichever it is not configured for.
  tsv             tsvector GENERATED ALWAYS AS (
                    setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
                    setweight(to_tsvector('simple', coalesce(body_md, '')), 'B')
                  ) STORED,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ
);
CREATE INDEX note_tsv_idx     ON memory.note USING gin (tsv);
CREATE INDEX note_parent_idx  ON memory.note (parent_id) WHERE deleted_at IS NULL;
CREATE INDEX note_person_idx  ON memory.note (person_id) WHERE person_id IS NOT NULL;
CREATE INDEX note_updated_idx ON memory.note (updated_at DESC) WHERE deleted_at IS NULL;

-- Undo history: a snapshot of the previous content, taken at most every ten
-- minutes of editing (autosave fires every second; a row per keystroke burst
-- would be noise). The newest 50 per note are kept.
CREATE TABLE memory.note_revision (
  id        BIGSERIAL PRIMARY KEY,
  note_id   UUID NOT NULL REFERENCES memory.note(id) ON DELETE CASCADE,
  title     TEXT NOT NULL,
  doc       JSONB,
  body_md   TEXT NOT NULL,
  saved_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX note_revision_note_idx ON memory.note_revision (note_id, saved_at DESC);

-- migrate:down

DROP TABLE IF EXISTS memory.note_revision;
DROP TABLE IF EXISTS memory.note;
