-- migrate:up

-- OAuth 2.1 state for the memory MCP connector (Claude Cowork / claude.ai).
-- Mirrors the curator connector's tables, schema-qualified. Claude registers
-- itself (dynamic client registration); the owner consents once behind
-- Authelia; tokens are stored hashed. Nothing here is seeded.
CREATE TABLE memory.oauth_client (
  client_id   TEXT PRIMARY KEY,
  info        JSONB NOT NULL,                 -- OAuthClientInformationFull as registered
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- An /authorize request parked while the owner is on the consent page.
CREATE TABLE memory.oauth_pending (
  id          TEXT PRIMARY KEY,               -- random, carried through the page
  client_id   TEXT NOT NULL REFERENCES memory.oauth_client(client_id) ON DELETE CASCADE,
  params      JSONB NOT NULL,                 -- AuthorizationParams
  expires_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE memory.oauth_code (
  code        TEXT PRIMARY KEY,
  client_id   TEXT NOT NULL REFERENCES memory.oauth_client(client_id) ON DELETE CASCADE,
  data        JSONB NOT NULL,                 -- AuthorizationCode
  expires_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE memory.oauth_token (
  token_hash  TEXT PRIMARY KEY,               -- sha256; the token itself is never stored
  kind        TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
  client_id   TEXT NOT NULL REFERENCES memory.oauth_client(client_id) ON DELETE CASCADE,
  scopes      JSONB NOT NULL DEFAULT '[]',
  resource    TEXT,
  expires_at  TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX oauth_token_client_idx ON memory.oauth_token (client_id);

-- migrate:down

DROP TABLE IF EXISTS memory.oauth_token;
DROP TABLE IF EXISTS memory.oauth_code;
DROP TABLE IF EXISTS memory.oauth_pending;
DROP TABLE IF EXISTS memory.oauth_client;
