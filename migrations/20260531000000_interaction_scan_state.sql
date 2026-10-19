-- migrate:up

-- Per-thread read watermark for the interaction scanner.
--
-- Replaces the old "scan everything active in the last 48h, top-40 busiest"
-- approach — which could crowd a quiet thread out of a run and, once its
-- messages aged past the 48h lookback, drop the item entirely (a follow-up
-- with Ivan surfaced ~2 days late and nearly not at all).
--
-- With a watermark, the scanner picks up ANY two-way thread whose latest
-- message is newer than scanned_through. A per-run cap still bounds cost,
-- but un-scanned threads simply stay candidates for the next run — nothing
-- is ever dropped by a time window.

CREATE TABLE memory.interaction_scan_state (
  person_id        UUID PRIMARY KEY REFERENCES canonical.person(id) ON DELETE CASCADE,
  -- The occurred_at of the newest message we've already scanned for this
  -- person. A thread is a candidate again only once it has a message newer
  -- than this.
  scanned_through  TIMESTAMPTZ NOT NULL,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down

DROP TABLE IF EXISTS memory.interaction_scan_state;
