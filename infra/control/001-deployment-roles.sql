-- Explicit fresh-database deployment bootstrap. No password or login is created.
-- Existing installations require reviewed migrations instead of replaying this.
BEGIN;
DO $$ BEGIN
    IF current_database() <> 'sentinelops_control' THEN
        RAISE EXCEPTION 'deployment bootstrap requires the selected control database';
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname IN ('control_owner','control_writer'))
       OR EXISTS (SELECT FROM pg_namespace WHERE nspname = 'sentinel_control') THEN
        RAISE EXCEPTION 'existing control roles/schema require explicit migration review';
    END IF;
END $$;
CREATE ROLE control_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE control_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
-- Only the separately authorized migration principal receives owner membership.
GRANT control_owner TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;
REVOKE ALL ON DATABASE sentinelops_control FROM PUBLIC;
GRANT CONNECT ON DATABASE sentinelops_control TO control_writer;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA sentinel_control AUTHORIZATION control_owner;
REVOKE ALL ON SCHEMA sentinel_control FROM PUBLIC;
GRANT USAGE ON SCHEMA sentinel_control TO control_writer;
COMMIT;
