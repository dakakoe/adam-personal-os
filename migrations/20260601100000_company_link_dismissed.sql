-- migrate:up

-- Dismissals for the LinkedIn link-review queue. The queue proposes person↔
-- company links from FUZZY LinkedIn-employer matches (e.g. "CoinPost Inc." →
-- entity "CoinPost") — high-noise, so they're reviewed, not auto-linked.
-- Accepting a suggestion writes a normal company_person row; dismissing it
-- records a tombstone here so the same (person, company) pair stops resurfacing.
CREATE TABLE memory.company_link_dismissed (
  person_id    UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE,
  company_id   UUID NOT NULL REFERENCES memory.company(id) ON DELETE CASCADE,
  dismissed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (person_id, company_id)
);

-- migrate:down

DROP TABLE IF EXISTS memory.company_link_dismissed;
