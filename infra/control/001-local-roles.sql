-- Sandbox bootstrap only. Production roles/secrets are provisioned separately.
CREATE ROLE control_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE control_writer LOGIN PASSWORD 'local-control-writer'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
REVOKE ALL ON DATABASE sentinelops_control FROM PUBLIC;
GRANT CONNECT ON DATABASE sentinelops_control TO control_writer;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA sentinel_control AUTHORIZATION control_owner;
REVOKE ALL ON SCHEMA sentinel_control FROM PUBLIC;
GRANT USAGE ON SCHEMA sentinel_control TO control_writer;
