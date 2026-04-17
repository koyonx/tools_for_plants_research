-- ---------------------------------------------------------------------------
-- Supabase self-hosted Postgres bootstrap
-- Creates roles that GoTrue / PostgREST / Storage expect to exist.
-- Based on the official Supabase self-hosted docker initdb scripts.
-- ---------------------------------------------------------------------------

-- anon: unauthenticated role (JWT with role=anon uses it via PostgREST)
-- authenticated: logged-in users (JWT with role=authenticated)
-- service_role: bypasses RLS — used by backend for privileged ops
-- authenticator: login role that PostgREST logs in as; switches into the
--                request-specific role via SET ROLE

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'service_role') THEN
    CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticator') THEN
    EXECUTE format(
      'CREATE ROLE authenticator NOINHERIT LOGIN PASSWORD %L',
      current_setting('custom.authenticator_password', true)
    );
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'supabase_auth_admin') THEN
    EXECUTE format(
      'CREATE ROLE supabase_auth_admin LOGIN CREATEROLE PASSWORD %L',
      current_setting('custom.auth_admin_password', true)
    );
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'supabase_storage_admin') THEN
    EXECUTE format(
      'CREATE ROLE supabase_storage_admin LOGIN CREATEROLE PASSWORD %L',
      current_setting('custom.storage_admin_password', true)
    );
  END IF;
END$$;

GRANT anon, authenticated, service_role TO authenticator;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated, service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON TABLES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON SEQUENCES TO anon, authenticated, service_role;

-- Schemas owned by service-specific admins; the services run their own
-- migrations to populate tables on first boot.
CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION supabase_auth_admin;
CREATE SCHEMA IF NOT EXISTS storage AUTHORIZATION supabase_storage_admin;

GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA storage TO anon, authenticated, service_role;

-- Extensions commonly used across Supabase services.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- ---------------------------------------------------------------------------
-- Auth helper functions (auth.uid / auth.role / auth.email / auth.jwt)
--
-- The hosted / `supabase/postgres` image ships these out of the box; we are
-- on plain `postgres:15-alpine`, so they need to exist *before* 01-schema.sql
-- creates any RLS policy that references them (CREATE POLICY parses and
-- type-checks the expression immediately — missing functions abort initdb).
--
-- Definitions mirror what PostgREST injects per request via
-- `request.jwt.claims`; values are STABLE so the planner can cache them
-- within a single query.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION auth.jwt()
RETURNS jsonb
LANGUAGE sql STABLE
AS $$
  SELECT COALESCE(
    nullif(current_setting('request.jwt.claim', true), ''),
    nullif(current_setting('request.jwt.claims', true), '')
  )::jsonb
$$;

CREATE OR REPLACE FUNCTION auth.uid()
RETURNS uuid
LANGUAGE sql STABLE
AS $$
  SELECT COALESCE(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid
$$;

CREATE OR REPLACE FUNCTION auth.role()
RETURNS text
LANGUAGE sql STABLE
AS $$
  SELECT COALESCE(
    nullif(current_setting('request.jwt.claim.role', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role')
  )::text
$$;

CREATE OR REPLACE FUNCTION auth.email()
RETURNS text
LANGUAGE sql STABLE
AS $$
  SELECT COALESCE(
    nullif(current_setting('request.jwt.claim.email', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'email')
  )::text
$$;

GRANT EXECUTE ON FUNCTION auth.jwt(), auth.uid(), auth.role(), auth.email()
  TO anon, authenticated, service_role;

-- GoTrue runs its own migration `00_init_auth_schema.up.sql` as
-- `supabase_auth_admin` on first boot and re-declares auth.uid / auth.role
-- via `CREATE OR REPLACE FUNCTION`, which requires ownership.  Transfer
-- ownership now so that migration succeeds and auth/storage can come up.
ALTER FUNCTION auth.jwt()   OWNER TO supabase_auth_admin;
ALTER FUNCTION auth.uid()   OWNER TO supabase_auth_admin;
ALTER FUNCTION auth.role()  OWNER TO supabase_auth_admin;
ALTER FUNCTION auth.email() OWNER TO supabase_auth_admin;

-- storage-api and GoTrue both run schema migrations as their own admin
-- roles; they need CREATE privileges on the current database to set up
-- functions/sequences outside their authorized schema.
DO $$
BEGIN
  EXECUTE format('GRANT ALL PRIVILEGES ON DATABASE %I TO supabase_auth_admin',
                 current_database());
  EXECUTE format('GRANT ALL PRIVILEGES ON DATABASE %I TO supabase_storage_admin',
                 current_database());
END$$;
