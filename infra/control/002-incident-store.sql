-- Run as the migration/bootstrap role, never as the application writer.
BEGIN;
SET LOCAL ROLE control_owner;
CREATE TABLE sentinel_control.schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE sentinel_control.incidents (
    incident_id TEXT PRIMARY KEY,
    current_revision BIGINT NOT NULL CHECK (current_revision > 0),
    lifecycle_stage TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE sentinel_control.incident_versions (
    incident_id TEXT NOT NULL REFERENCES sentinel_control.incidents(incident_id),
    revision BIGINT NOT NULL CHECK (revision > 0),
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
    payload JSONB NOT NULL,
    context JSONB NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (incident_id, revision)
);
ALTER TABLE sentinel_control.incidents ADD CONSTRAINT current_incident_version
    FOREIGN KEY (incident_id,current_revision)
    REFERENCES sentinel_control.incident_versions(incident_id,revision)
    DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE sentinel_control.incident_events (
    event_id UUID PRIMARY KEY,
    incident_id TEXT NOT NULL,
    revision BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    lifecycle_stage TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (incident_id,revision)
        REFERENCES sentinel_control.incident_versions(incident_id,revision),
    UNIQUE (incident_id,revision)
);
CREATE TABLE sentinel_control.outbox (
    event_id UUID PRIMARY KEY REFERENCES sentinel_control.incident_events(event_id),
    topic TEXT NOT NULL,
    body JSONB NOT NULL,
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    completed_at TIMESTAMPTZ,
    CHECK ((lease_token IS NULL) = (lease_until IS NULL))
);
CREATE INDEX outbox_pending ON sentinel_control.outbox(available_at,event_id)
    WHERE completed_at IS NULL;
GRANT SELECT,INSERT ON sentinel_control.incidents TO control_writer;
GRANT UPDATE(current_revision,lifecycle_stage,updated_at)
    ON sentinel_control.incidents TO control_writer;
GRANT SELECT,INSERT ON sentinel_control.incident_versions,
    sentinel_control.incident_events,sentinel_control.outbox TO control_writer;
GRANT UPDATE(lease_token,lease_until,attempts,completed_at)
    ON sentinel_control.outbox TO control_writer;
GRANT SELECT ON sentinel_control.schema_migrations TO control_writer;
INSERT INTO sentinel_control.schema_migrations(version) VALUES (1);
COMMIT;
