-- migrate:up

-- People the user has explicitly ruled OUT of the /prospects reconnect list
-- (family, personal friends, dead leads). The ranking is signal-only (interaction
-- volume × dormancy) and can't tell "business worth reviving" from "my spouse",
-- so dismissing is how the user teaches it. A dismissed person just drops from
-- the list; nothing else about them changes.
CREATE TABLE memory.prospect_dismissed (
  person_id     UUID PRIMARY KEY REFERENCES canonical.person(id) ON DELETE CASCADE,
  dismissed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down

DROP TABLE IF EXISTS memory.prospect_dismissed;
