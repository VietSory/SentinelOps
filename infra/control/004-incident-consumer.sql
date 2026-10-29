-- Additive local migration, executed only by the selected bootstrap owner.
BEGIN;
SET LOCAL ROLE control_owner;
CREATE TABLE IF NOT EXISTS sentinel_control.incident_consumers (
    consumer_id TEXT PRIMARY KEY CHECK (consumer_id ~ '^[0-9a-f]{64}$'),
    health_stream_id TEXT NOT NULL UNIQUE REFERENCES sentinel_control.health_streams(stream_id),
    identity JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS sentinel_control.incident_consumer_steps (
    consumer_id TEXT NOT NULL REFERENCES sentinel_control.incident_consumers(consumer_id),
    bucket_end TIMESTAMPTZ NOT NULL,
    health_batch_ref TEXT NOT NULL REFERENCES sentinel_control.health_batches(manifest_ref),
    state_ref TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (consumer_id,bucket_end),
    UNIQUE (consumer_id,state_ref)
);
CREATE TABLE IF NOT EXISTS sentinel_control.incident_consumer_checkpoints (
    consumer_id TEXT PRIMARY KEY REFERENCES sentinel_control.incident_consumers(consumer_id),
    last_state_ref TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (consumer_id,last_state_ref)
        REFERENCES sentinel_control.incident_consumer_steps(consumer_id,state_ref)
);
CREATE TABLE IF NOT EXISTS sentinel_control.incident_jobs (
    job_id TEXT PRIMARY KEY CHECK (job_id ~ '^[0-9a-f]{64}$'),
    consumer_id TEXT NOT NULL REFERENCES sentinel_control.incident_consumers(consumer_id),
    group_id TEXT NOT NULL UNIQUE CHECK (group_id ~ '^[0-9a-f]{64}$'),
    closing_bucket_end TIMESTAMPTZ NOT NULL,
    incident_id TEXT NOT NULL UNIQUE,
    intent_ref TEXT NOT NULL UNIQUE,
    detected_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (job_id,incident_id),
    FOREIGN KEY (consumer_id,closing_bucket_end)
        REFERENCES sentinel_control.incident_consumer_steps(consumer_id,bucket_end)
);
CREATE INDEX IF NOT EXISTS incident_jobs_consumer ON sentinel_control.incident_jobs(consumer_id,closing_bucket_end,job_id);
CREATE TABLE IF NOT EXISTS sentinel_control.incident_episode_recoveries (
    consumer_id TEXT NOT NULL REFERENCES sentinel_control.incident_consumers(consumer_id),
    episode_key TEXT NOT NULL CHECK (episode_key ~ '^[0-9a-f]{64}$'),
    service TEXT NOT NULL CHECK (service IN ('checkout-service','payment-service','inventory-service')),
    episode_start_at TIMESTAMPTZ NOT NULL,
    bucket_end TIMESTAMPTZ NOT NULL,
    signal_id TEXT NOT NULL REFERENCES sentinel_control.health_signals(signal_id),
    signal_ref TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (consumer_id,episode_key),
    UNIQUE (consumer_id,signal_id)
);
CREATE TABLE IF NOT EXISTS sentinel_control.incident_job_evidence (
    job_id TEXT PRIMARY KEY REFERENCES sentinel_control.incident_jobs(job_id),
    capture_ref TEXT NOT NULL,
    result_ref TEXT NOT NULL,
    analyzed_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS sentinel_control.incident_commands (
    command_id TEXT PRIMARY KEY CHECK (command_id ~ '^[0-9a-f]{64}$'),
    job_id TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    expected_revision BIGINT NOT NULL CHECK (expected_revision >= 0),
    lifecycle_stage TEXT NOT NULL CHECK (lifecycle_stage IN ('OPEN','RCA_READY','ESCALATED','VERIFYING','RECOVERED','CLOSED')),
    command_ref TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (job_id,incident_id) REFERENCES sentinel_control.incident_jobs(job_id,incident_id),
    UNIQUE (command_id,incident_id,expected_revision),
    UNIQUE (job_id,expected_revision)
);
CREATE TABLE IF NOT EXISTS sentinel_control.incident_command_receipts (
    command_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision > 0),
    expected_revision BIGINT GENERATED ALWAYS AS (revision - 1) STORED,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (incident_id,revision) REFERENCES sentinel_control.incident_versions(incident_id,revision),
    FOREIGN KEY (command_id,incident_id,expected_revision)
        REFERENCES sentinel_control.incident_commands(command_id,incident_id,expected_revision)
);
GRANT SELECT,INSERT ON sentinel_control.incident_consumers, sentinel_control.incident_consumer_steps,
    sentinel_control.incident_consumer_checkpoints, sentinel_control.incident_jobs,
    sentinel_control.incident_episode_recoveries, sentinel_control.incident_job_evidence,
    sentinel_control.incident_commands, sentinel_control.incident_command_receipts TO control_writer;
GRANT UPDATE(last_state_ref,updated_at) ON sentinel_control.incident_consumer_checkpoints TO control_writer;
INSERT INTO sentinel_control.schema_migrations(version) VALUES (3) ON CONFLICT DO NOTHING;
COMMIT;
