-- migrate:up

-- A profile summary is an LLM synthesis of a person's INPUTS (interactions +
-- identity bios: LinkedIn role/company, Google org, etc.). It was only ever
-- rebuilt on a first-build or an age-based refresh, so attaching new info AFTER
-- the build (e.g. importing LinkedIn — "mid-level manager" → "CPTO at Ex-human")
-- left the summary stale for up to the refresh interval. input_sig fingerprints
-- those inputs so the builder can rebuild the moment they change.
ALTER TABLE memory.profile ADD COLUMN IF NOT EXISTS input_sig TEXT;

-- Baseline every existing profile to its CURRENT input signature (no rebuild) —
-- must stay byte-identical to profile_builder.main._sig_sql().
UPDATE memory.profile mp SET input_sig = md5(
  (SELECT count(*)::text FROM canonical.interaction WHERE person_id = mp.person_id)
  || '|' ||
  coalesce((SELECT string_agg(i.source || ':' || i.source_id || ':' || coalesce(i.evidence::text, ''), '§' ORDER BY i.id)
              FROM canonical.identity i WHERE i.person_id = mp.person_id), '')
);

-- …but FORCE a rebuild of any profile whose inputs grew AFTER it was last built:
-- an identity (a LinkedIn/email/phone row — enrichment) arrived post-build. NULL
-- signature → the refresh job re-fuses it on the next run. This corrects the
-- already-stale summaries without a blanket ~5.5k re-run.
UPDATE memory.profile mp SET input_sig = NULL
 WHERE mp.last_built_at IS NULL
    OR EXISTS (SELECT 1 FROM canonical.identity i
                WHERE i.person_id = mp.person_id AND i.created_at > mp.last_built_at);

-- migrate:down

ALTER TABLE memory.profile DROP COLUMN IF EXISTS input_sig;
