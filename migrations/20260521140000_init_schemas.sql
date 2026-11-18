-- migrate:up

-- Extensions live in public; created here so all downstream schemas can use them.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "vector";     -- pgvector

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS canonical;
CREATE SCHEMA IF NOT EXISTS memory;
CREATE SCHEMA IF NOT EXISTS queue;

-- Shared trigger function: stamps NEW.updated_at = now() on UPDATE.
CREATE OR REPLACE FUNCTION public._touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

-- migrate:down

DROP FUNCTION IF EXISTS public._touch_updated_at();
DROP SCHEMA IF EXISTS queue CASCADE;
DROP SCHEMA IF EXISTS memory CASCADE;
DROP SCHEMA IF EXISTS canonical CASCADE;
DROP SCHEMA IF EXISTS raw CASCADE;
-- Extensions intentionally left in place; other databases may share them.
