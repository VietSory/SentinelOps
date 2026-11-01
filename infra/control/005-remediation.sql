-- Explicit optional migration. Separate version ledger keeps core v1/v2/v3 intact.
-- Bootstrap executes this; application/model/incident roles receive no action grants.
BEGIN;
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='control_action_writer') THEN
        CREATE ROLE control_action_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='control_action_reader') THEN
        CREATE ROLE control_action_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS sentinel_remediation AUTHORIZATION control_owner;
REVOKE ALL ON SCHEMA sentinel_remediation FROM PUBLIC;
GRANT CONNECT ON DATABASE sentinelops_control TO control_action_writer;
GRANT CONNECT ON DATABASE sentinelops_control TO control_action_reader;
GRANT USAGE ON SCHEMA sentinel_remediation TO control_action_reader;
GRANT USAGE ON SCHEMA sentinel_control, sentinel_remediation TO control_action_writer;
GRANT SELECT ON sentinel_control.incidents, sentinel_control.incident_versions TO control_action_writer;
GRANT SELECT ON sentinel_control.schema_migrations, sentinel_control.health_streams,
    sentinel_control.health_batches, sentinel_control.health_signals,
    sentinel_control.incident_consumers, sentinel_control.incident_consumer_steps,
    sentinel_control.incident_consumer_checkpoints, sentinel_control.incident_jobs,
    sentinel_control.incident_job_evidence, sentinel_control.incident_episode_recoveries,
    sentinel_control.incident_commands, sentinel_control.incident_command_receipts TO control_action_writer;
SET LOCAL ROLE control_owner;
CREATE TABLE IF NOT EXISTS sentinel_remediation.schema_migrations (
    version INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS sentinel_remediation.intents (
    action_id TEXT PRIMARY KEY CHECK (action_id ~ '^[0-9a-f]{64}$'),
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    intent_hash TEXT NOT NULL CHECK (intent_hash ~ '^[0-9a-f]{64}$'),
    target_key TEXT NOT NULL CHECK (target_key ~ '^[0-9a-f]{64}$'),
    incident_id TEXT NOT NULL,
    incident_revision BIGINT NOT NULL,
    payload JSONB NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (incident_id,incident_revision) REFERENCES sentinel_control.incident_versions(incident_id,revision),
    UNIQUE (action_id,target_key,incident_id)
);
CREATE TABLE IF NOT EXISTS sentinel_remediation.states (
    action_id TEXT PRIMARY KEY REFERENCES sentinel_remediation.intents(action_id),
    revision INTEGER NOT NULL CHECK (revision>0),
    phase TEXT NOT NULL CHECK (phase IN ('prepared','blocked','dispatching','verifying','recovered','escalated')),
    facts JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS sentinel_remediation.events (
    action_id TEXT NOT NULL REFERENCES sentinel_remediation.intents(action_id),
    revision INTEGER NOT NULL CHECK (revision>0),
    phase TEXT NOT NULL CHECK (phase IN ('prepared','blocked','dispatching','verifying','recovered','escalated')),
    facts JSONB NOT NULL,
    previous_hash TEXT,
    event_hash TEXT NOT NULL CHECK (event_hash ~ '^[0-9a-f]{64}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (action_id,revision),
    UNIQUE (action_id,event_hash)
);
CREATE TABLE IF NOT EXISTS sentinel_remediation.targets (
    target_key TEXT PRIMARY KEY,
    active_action TEXT REFERENCES sentinel_remediation.intents(action_id),
    last_dispatch_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS sentinel_remediation.dispatches (
    action_id TEXT PRIMARY KEY,
    target_key TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (action_id,target_key,incident_id)
        REFERENCES sentinel_remediation.intents(action_id,target_key,incident_id),
    UNIQUE (incident_id,target_key)
);
CREATE INDEX IF NOT EXISTS action_incident_history ON sentinel_remediation.intents(incident_id,recorded_at,action_id);
GRANT SELECT ON ALL TABLES IN SCHEMA sentinel_remediation TO control_action_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA sentinel_remediation TO control_action_writer;
GRANT INSERT ON sentinel_remediation.intents, sentinel_remediation.states,
    sentinel_remediation.events, sentinel_remediation.targets, sentinel_remediation.dispatches TO control_action_writer;
GRANT UPDATE(revision,phase,facts,updated_at) ON sentinel_remediation.states TO control_action_writer;
GRANT UPDATE(active_action,last_dispatch_at) ON sentinel_remediation.targets TO control_action_writer;
INSERT INTO sentinel_remediation.schema_migrations(version) VALUES(1) ON CONFLICT DO NOTHING;
COMMIT;
