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
