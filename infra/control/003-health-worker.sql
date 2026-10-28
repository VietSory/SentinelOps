-- Additive local migration; run as bootstrap/migration owner, never application writer.
BEGIN;
SET LOCAL ROLE control_owner;
CREATE TABLE IF NOT EXISTS sentinel_control.health_streams (
    stream_id TEXT PRIMARY KEY CHECK (stream_id ~ '^[0-9a-f]{64}$'),
    identity JSONB NOT NULL,
    initial_bucket_end TIMESTAMPTZ NOT NULL,
    last_bucket_end TIMESTAMPTZ,
    last_batch_ref TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((last_bucket_end IS NULL) = (last_batch_ref IS NULL)),
    CHECK (last_bucket_end IS NULL OR last_bucket_end >= initial_bucket_end)
);
CREATE TABLE IF NOT EXISTS sentinel_control.health_batches (
    stream_id TEXT NOT NULL REFERENCES sentinel_control.health_streams(stream_id),
    bucket_end TIMESTAMPTZ NOT NULL,
    manifest_ref TEXT NOT NULL UNIQUE,
    evaluated_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (stream_id,bucket_end)
);
CREATE TABLE IF NOT EXISTS sentinel_control.health_signals (
    stream_id TEXT NOT NULL,
    bucket_end TIMESTAMPTZ NOT NULL,
    service TEXT NOT NULL,
    signal_id TEXT NOT NULL UNIQUE CHECK (signal_id ~ '^[0-9a-f]{64}$'),
    signal_ref TEXT NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (stream_id,bucket_end,service),
    FOREIGN KEY (stream_id,bucket_end) REFERENCES sentinel_control.health_batches(stream_id,bucket_end)
);
GRANT SELECT,INSERT ON sentinel_control.health_streams, sentinel_control.health_batches,
    sentinel_control.health_signals TO control_writer;
GRANT UPDATE(last_bucket_end,last_batch_ref,updated_at) ON sentinel_control.health_streams TO control_writer;
INSERT INTO sentinel_control.schema_migrations(version) VALUES (2) ON CONFLICT DO NOTHING;
COMMIT;
