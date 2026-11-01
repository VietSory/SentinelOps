-- Explicit optional action migration 2. No core/IER mutation or dispatch grant.
BEGIN;
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='control_action_resolver') THEN
        CREATE ROLE control_action_resolver NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END $$;
GRANT CONNECT ON DATABASE sentinelops_control TO control_action_resolver;
GRANT USAGE ON SCHEMA sentinel_control,sentinel_remediation TO control_action_resolver;
GRANT SELECT ON sentinel_control.health_streams,sentinel_control.health_batches,
    sentinel_control.health_signals TO control_action_resolver;
SET LOCAL ROLE control_owner;
CREATE TABLE IF NOT EXISTS sentinel_remediation.quarantine_releases (
    action_id TEXT PRIMARY KEY REFERENCES sentinel_remediation.intents(action_id),
    resolution_id TEXT NOT NULL UNIQUE CHECK (resolution_id ~ '^[0-9a-f]{64}$'),
    review_ref TEXT NOT NULL UNIQUE CHECK (review_ref ~ '^artifact://manifests/[0-9a-f]{64}\.json$'),
    idempotency_key TEXT NOT NULL UNIQUE CHECK (idempotency_key ~ '^[A-Za-z0-9_-]{16,128}$'),
    action_revision INTEGER NOT NULL CHECK (action_revision>0),
    terminal_event_hash TEXT NOT NULL CHECK (terminal_event_hash ~ '^[0-9a-f]{64}$'),
    target_key TEXT NOT NULL REFERENCES sentinel_remediation.targets(target_key),
    operator_actor NAME NOT NULL,
    payload JSONB NOT NULL,
    payload_sha256 TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
GRANT SELECT ON ALL TABLES IN SCHEMA sentinel_remediation TO control_action_resolver;
GRANT SELECT ON sentinel_remediation.quarantine_releases TO control_action_reader,control_action_writer;

CREATE OR REPLACE FUNCTION sentinel_remediation.release_quarantine(
    p_action TEXT,p_revision INTEGER,p_event_hash TEXT,p_review_ref TEXT,p_key TEXT,p_payload JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET timezone='UTC' AS $$
DECLARE
    v_state sentinel_remediation.states%ROWTYPE;
    v_intent sentinel_remediation.intents%ROWTYPE;
    v_target sentinel_remediation.targets%ROWTYPE;
    v_dispatch sentinel_remediation.dispatches%ROWTYPE;
    v_event sentinel_remediation.events%ROWTYPE;
    v_receipt sentinel_remediation.quarantine_releases%ROWTYPE;
    v_now TIMESTAMPTZ;
BEGIN
    -- Attribution comes from the authenticated DB login, never a request actor.
    IF NOT pg_has_role(session_user,'control_action_resolver','MEMBER')
       OR pg_has_role(session_user,'control_owner','MEMBER') THEN
        RAISE EXCEPTION 'dedicated quarantine operator required' USING ERRCODE='42501';
    END IF;
    IF p_action IS NULL OR p_event_hash IS NULL OR p_review_ref IS NULL OR p_key IS NULL OR p_revision IS NULL
       OR p_action !~ '^[0-9a-f]{64}$' OR p_event_hash !~ '^[0-9a-f]{64}$'
       OR p_review_ref !~ '^artifact://manifests/[0-9a-f]{64}\.json$'
       OR p_key !~ '^[A-Za-z0-9_-]{16,128}$' OR p_revision<1 THEN
        RAISE EXCEPTION 'invalid quarantine request' USING ERRCODE='23514';
    END IF;
    SELECT * INTO v_state FROM sentinel_remediation.states WHERE action_id=p_action FOR UPDATE;
    SELECT * INTO v_intent FROM sentinel_remediation.intents WHERE action_id=p_action;
    IF v_state.action_id IS NULL OR v_intent.action_id IS NULL THEN
        RAISE EXCEPTION 'action not found' USING ERRCODE='40001';
    END IF;
    SELECT * INTO v_target FROM sentinel_remediation.targets WHERE target_key=v_intent.target_key FOR UPDATE;
    SELECT * INTO v_receipt FROM sentinel_remediation.quarantine_releases WHERE action_id=p_action OR idempotency_key=p_key;
    IF v_receipt.action_id IS NOT NULL THEN
        IF v_receipt.action_id=p_action AND v_receipt.review_ref=p_review_ref
           AND v_receipt.action_revision=p_revision AND v_receipt.terminal_event_hash=p_event_hash
           AND v_receipt.idempotency_key=p_key AND v_receipt.operator_actor=session_user THEN
            RETURN to_jsonb(v_receipt);
        END IF;
        RAISE EXCEPTION 'quarantine idempotency conflict' USING ERRCODE='40001';
    END IF;
    SELECT * INTO v_dispatch FROM sentinel_remediation.dispatches WHERE action_id=p_action;
    SELECT * INTO v_event FROM sentinel_remediation.events WHERE action_id=p_action ORDER BY revision DESC LIMIT 1;
    IF v_state.phase<>'escalated' OR v_state.revision<>p_revision
       OR v_event.action_id IS NULL OR v_event.revision<>p_revision OR v_event.phase<>'escalated'
       OR v_event.event_hash<>p_event_hash OR v_event.facts<>v_state.facts
       OR v_target.active_action IS DISTINCT FROM p_action
       OR v_dispatch.action_id IS NULL OR v_dispatch.target_key<>v_target.target_key THEN
        RAISE EXCEPTION 'quarantine state changed' USING ERRCODE='40001';
    END IF;
    v_now:=clock_timestamp();
    IF p_payload IS NULL OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'invalid quarantine proof shape' USING ERRCODE='23514';
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(p_payload))<>16
       OR NOT p_payload ?& ARRAY['release_version','action_id','action_revision','terminal_event_hash',
           'target_key','review_ref','dispatch_authorized','recovery_accepted','ier_mutated',
           'opened_at','observed_at','verification_as_of','last_bucket_end','before_ref','after_ref','health_ref']
       OR (SELECT bool_and(jsonb_typeof(p_payload->k)='string') FROM unnest(ARRAY[
           'release_version','action_id','terminal_event_hash','target_key','review_ref',
           'opened_at','observed_at','verification_as_of','last_bucket_end','before_ref','after_ref','health_ref']) AS k) IS DISTINCT FROM TRUE
       OR jsonb_typeof(p_payload->'action_revision') IS DISTINCT FROM 'number'
       OR p_payload->>'before_ref' !~ '^artifact://observations/[0-9a-f]{64}\.json$'
       OR p_payload->>'after_ref' !~ '^artifact://observations/[0-9a-f]{64}\.json$'
       OR p_payload->>'health_ref' !~ '^artifact://manifests/[0-9a-f]{64}\.json$'
       OR p_payload->>'release_version' IS DISTINCT FROM 'manual-quarantine-release/v1'
       OR p_payload->>'action_id' IS DISTINCT FROM p_action
       OR p_payload->>'action_revision' IS DISTINCT FROM p_revision::text
       OR p_payload->>'terminal_event_hash' IS DISTINCT FROM p_event_hash
       OR p_payload->>'target_key' IS DISTINCT FROM v_target.target_key
       OR p_payload->>'review_ref' IS DISTINCT FROM p_review_ref
       OR p_payload->'dispatch_authorized' IS DISTINCT FROM 'false'::jsonb
       OR p_payload->'recovery_accepted' IS DISTINCT FROM 'false'::jsonb
       OR p_payload->'ier_mutated' IS DISTINCT FROM 'false'::jsonb
       OR (p_payload->>'observed_at')::timestamptz > v_now
       OR (p_payload->>'verification_as_of')::timestamptz > (p_payload->>'observed_at')::timestamptz
       OR (p_payload->>'opened_at')::timestamptz > (p_payload->>'verification_as_of')::timestamptz
       OR v_now-(p_payload->>'verification_as_of')::timestamptz > interval '30 seconds'
       OR v_now-(p_payload->>'opened_at')::timestamptz > interval '600 seconds'
       OR (p_payload->>'opened_at')::timestamptz < v_dispatch.recorded_at+interval '600 seconds'
       OR (p_payload->>'last_bucket_end')::timestamptz <> date_trunc('minute',v_now-interval '30 seconds')
       OR (p_payload->>'last_bucket_end')::timestamptz < (p_payload->>'opened_at')::timestamptz+interval '180 seconds'
       OR v_now-v_dispatch.recorded_at < interval '600 seconds' THEN
        RAISE EXCEPTION 'quarantine proof unavailable stale or premature' USING ERRCODE='23514';
    END IF;
    INSERT INTO sentinel_remediation.quarantine_releases(
        action_id,resolution_id,review_ref,idempotency_key,action_revision,terminal_event_hash,target_key,
        operator_actor,payload,payload_sha256)
    VALUES(p_action,substring(p_review_ref from 22 for 64),p_review_ref,p_key,p_revision,p_event_hash,v_target.target_key,
        session_user,p_payload,encode(sha256(convert_to(p_payload::text,'UTF8')),'hex')) RETURNING * INTO v_receipt;
    -- No action phase, IER, dispatch budget or cooldown clock is rewritten.
    UPDATE sentinel_remediation.targets SET active_action=NULL WHERE target_key=v_target.target_key AND active_action=p_action;
    RETURN to_jsonb(v_receipt);
END $$;
REVOKE ALL ON FUNCTION sentinel_remediation.release_quarantine(TEXT,INTEGER,TEXT,TEXT,TEXT,JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sentinel_remediation.release_quarantine(TEXT,INTEGER,TEXT,TEXT,TEXT,JSONB) TO control_action_resolver;
INSERT INTO sentinel_remediation.schema_migrations(version) VALUES(2) ON CONFLICT DO NOTHING;
COMMIT;
