"""Actual PostgreSQL privilege checks; no live Kubernetes/health binding claim."""
from sentinelops.remediation.domain import Blocked
from sentinelops.remediation.image_review import require_evidence_reader


async def exercise(bootstrap, reader_pool, writer_pool):
    tables = 'sentinel_control.health_streams,sentinel_control.health_batches,sentinel_control.health_signals'
    await bootstrap.execute('GRANT USAGE ON SCHEMA sentinel_control TO control_action_reader')
    await bootstrap.execute('GRANT SELECT ON ' + tables + ' TO control_action_reader')
    await require_evidence_reader(reader_pool)
    for pool in (writer_pool,):
        try:
            await require_evidence_reader(pool)
            raise AssertionError('image evidence reader accepted writer privileges')
        except Blocked:
            pass
    await bootstrap.execute('REVOKE SELECT ON sentinel_control.health_signals FROM control_action_reader')
    try:
        await require_evidence_reader(reader_pool)
        raise AssertionError('image evidence reader accepted incomplete SELECT privileges')
    except Blocked:
        pass
    await bootstrap.execute('GRANT SELECT ON sentinel_control.health_signals TO control_action_reader')
    await bootstrap.execute('GRANT UPDATE(updated_at) ON sentinel_control.health_streams TO control_action_reader')
    try:
        await require_evidence_reader(reader_pool)
        raise AssertionError('image evidence reader accepted column UPDATE privileges')
    except Blocked:
        pass
    await bootstrap.execute('REVOKE UPDATE(updated_at) ON sentinel_control.health_streams FROM control_action_reader')
    function = 'sentinel_remediation.release_quarantine(text,integer,text,text,text,jsonb)'
    await bootstrap.execute('GRANT EXECUTE ON FUNCTION ' + function + ' TO control_action_reader')
    try:
        await require_evidence_reader(reader_pool)
        raise AssertionError('image evidence reader accepted guarded release authority')
    except Blocked:
        pass
    await bootstrap.execute('REVOKE EXECUTE ON FUNCTION ' + function + ' FROM control_action_reader')
    await require_evidence_reader(reader_pool)
    async with reader_pool.acquire() as conn:
        await conn.fetch('SELECT stream_id FROM sentinel_control.health_streams LIMIT 1')
    return 'image evidence SELECT-only readiness rejects writer, missing SELECT, column DML and release-function privileges'
