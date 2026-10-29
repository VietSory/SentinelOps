-- Additive migration: preserve accepted source/incident history. Apply as owner.
BEGIN;
SET LOCAL ROLE control_owner;
CREATE TABLE IF NOT EXISTS sentinel_control.incident_consumers (
    consumer_id TEXT PRIMARY KEY CHECK (consumer_id ~ '^[0-9a-f]{64}$'),
    stream_id TEXT NOT NULL REFERENCES sentinel_control.health_streams(stream_id),
    identity JSONB NOT NULL,
    last_bucket_end TIMESTAMPTZ,
    last_commit_ref TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((last_bucket_end IS NULL) = (last_commit_ref IS NULL))
);
CREATE TABLE IF NOT EXISTS sentinel_control.incident_batches (
    consumer_id TEXT NOT NULL REFERENCES sentinel_control.incident_consumers(consumer_id),
    stream_id TEXT NOT NULL,
    bucket_end TIMESTAMPTZ NOT NULL,
    commit_ref TEXT NOT NULL UNIQUE,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (consumer_id,bucket_end),
    FOREIGN KEY (stream_id,bucket_end) REFERENCES sentinel_control.health_batches(stream_id,bucket_end)
);
GRANT SELECT,INSERT ON sentinel_control.incident_consumers,sentinel_control.incident_batches TO control_writer;
GRANT UPDATE(last_bucket_end,last_commit_ref,updated_at) ON sentinel_control.incident_consumers TO control_writer;
INSERT INTO sentinel_control.schema_migrations(version) VALUES (3) ON CONFLICT DO NOTHING;
COMMIT;
