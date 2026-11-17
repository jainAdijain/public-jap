-- Runs once, when the db volume is first created.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- The role the app's sessions switch to for every transaction (PRD S7): it owns nothing, so
-- row-level security on the private tables applies to it. Migration 0013_rls creates it too
-- (a database that already exists never runs this file) and grants it what it needs.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jap_app') THEN
    CREATE ROLE jap_app NOLOGIN;
  END IF;
END $$;
