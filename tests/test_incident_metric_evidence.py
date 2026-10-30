import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.incident_metrics_evidence import IncidentMetricEvidence, expected_metrics, validate_protocol
from scripts.incident_worker_evidence import IncidentWorkerEvidence
from sentinelops.telemetry.core import BUSINESS_ROUTES
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.contracts import utc_time


def captures(tmp_path):
    store = ArtifactStore(tmp_path / 'objects')
    refs = []
    for minute, availability in [('00', 'UNKNOWN'), ('01', 'ELIGIBLE')]:
        refs.append(store.write('manifests', {'last_bucket_end': '2026-10-30T00:' + minute + ':00Z',
                    'service_states': {s: {'availability': availability} for s in BUSINESS_ROUTES}}))
    rca = store.write('signals', {'abstained': True})
    events = [{'status': 'INGESTED', 'state_ref': ref, 'bucket_end': store.read(ref)['last_bucket_end'],
               'created_jobs': ['fixture-job'] if i == 0 else []} for i, ref in enumerate(refs)]
    logs = [tmp_path / ('consumer-' + str(i) + '.log') for i in range(3)]
    initial = {'last_bucket_end': '2026-10-29T23:59:00Z',
               'service_states': {s: {'availability': 'UNKNOWN'} for s in BUSINESS_ROUTES}}
    initial_ref = store.write('manifests', initial)
    initial_event = {'status': 'INGESTED', 'state_ref': initial_ref, 'bucket_end': initial['last_bucket_end'],
                     'created_jobs': ['initial-job']}
    logs[0].write_text(json.dumps({'status': 'PROGRESS', 'processing_ms': 999., 'events': [initial_event,
                                     {'status': 'ENRICHED', 'result_ref': rca}]}) + '\n')
    logs[1].write_text(json.dumps({'status': 'PROGRESS', 'processing_ms': 125., 'events': events +
                                   [{'status': 'ENRICHED', 'result_ref': rca}]}) + '\n')
    logs[2].write_text(json.dumps({'status': 'BUSY'}) + '\n' + json.dumps({'status': 'DEPENDENCY_UNAVAILABLE'}) + '\n')
    return store, [initial_ref, *refs], events, logs


def test_counter_arithmetic_uses_each_current_process_event_and_durable_ref(tmp_path):
    store, refs, _, logs = captures(tmp_path)
    totals, latest = expected_metrics(logs, store, accepted_refs=set(refs))
    assert totals['commits'] == {'incidents-0': 2}
    assert totals['groups'] == {'incidents-0': 1} and totals['abstentions'] == {'incidents-0': 1}
    assert totals['cycles'] == {'incidents-0': 1} and totals['duration'] == {'incidents-0': .125}
    assert totals['busy'] == {'incidents-1': 1} and totals['errors'] == {'incidents-1': 1}
    assert totals['unknown'] == {('incidents-0', s): 1 for s in BUSINESS_ROUTES}
    assert latest['incidents-0'] == store.read(refs[-1])


def test_orphan_hash_valid_state_and_duplicate_current_process_ingestion_are_rejected(tmp_path):
    store, refs, events, logs = captures(tmp_path)
    with pytest.raises(AssertionError, match='durable consumer journal'):
        expected_metrics(logs, store, accepted_refs={refs[-1]})
    logs[2].write_text(json.dumps({'status': 'PROGRESS', 'processing_ms': 1., 'events': [events[0]]}) + '\n')
    with pytest.raises(AssertionError, match='printed twice'):
        expected_metrics(logs, store, accepted_refs=set(refs))


def test_profile_cannot_shorten_required_pauses_or_claim_full_acceptance():
    value = json.loads(Path('configs/incident_metrics_acceptance.json').read_text())
    validate_protocol(value)
    for key, replacement in [('backlog_pause_seconds', 1), ('collector_outage_seconds', 0),
                             ('minimum_backlog_steps', 1), ('actions_allowed', True),
                             ('full_NFR10_accepted', True), ('require_completed_progress_before_crash', False)]:
        changed = deepcopy(value); changed[key] = replacement
        with pytest.raises(ValueError): validate_protocol(changed)


def test_incident_latency_uses_progress_guard_and_cold_slot_rejects_borrowed_source(tmp_path):
    evidence = IncidentMetricEvidence(tmp_path / 'metrics')
    assert evidence.duration_guard == 'sentinelops_incident_has_local_progress'
    assert evidence.instance_pattern == 'incidents-[01]'
    snapshot = {'series': {'has_decision': [
        {'metric': {'worker_instance': 'incidents-0'}, 'value': [0, '1']},
        {'metric': {'worker_instance': 'incidents-1'}, 'value': [0, '0']}]}}
    evidence.ready = AsyncMock(return_value=snapshot)
    evidence.guarded_source = AsyncMock(return_value={'source': [
        {'metric': {'worker_instance': 'incidents-1'}, 'value': [0, '1']}]})
    with pytest.raises(AssertionError): asyncio.run(evidence.restarted())


def test_native_observer_refuses_nonowned_metrics_endpoint_before_output(tmp_path):
    with pytest.raises(ValueError):
        IncidentWorkerEvidence(tmp_path / 'run', ArtifactStore(tmp_path / 'source'), 'stream', 'project',
                               metrics_endpoint='http://example.invalid:4317')
    assert not (tmp_path / 'run').exists()


@pytest.mark.parametrize('count', [0, 1])
def test_completed_progress_before_crash_requires_positive_exported_group_and_abstention(tmp_path, count):
    evidence = IncidentMetricEvidence(tmp_path / 'metrics')
    def point(v): return [{'metric': {'worker_instance': 'incidents-0'}, 'value': [100, str(v)]}]
    snapshot = {'observed_at_unix': 105, 'series': {'heartbeat': point(100), 'ready': point(1),
                                                 'groups': point(count), 'abstentions': point(count)}}
    async def wait(name, predicate):
        assert predicate(snapshot), 'group or abstention branch was never observed'
        return snapshot
    evidence.wait = wait
    if count == 0:
        with pytest.raises(AssertionError): asyncio.run(evidence.completed_before_crash())
    else:
        assert asyncio.run(evidence.completed_before_crash()) == snapshot


@pytest.mark.parametrize('drop_first_commit', [False, True])
def test_final_snapshot_matches_every_counter_duration_and_latest_local_source(tmp_path, drop_first_commit):
    store, refs, _, logs = captures(tmp_path)
    evidence = IncidentMetricEvidence(tmp_path / 'metrics')
    def points(values):
        return [{'metric': {'worker_instance': s}, 'value': [100, str(v)]} for s, v in values.items()]
    series = {k: points(v) for k, v in {
        'heartbeat': {'incidents-0': 100, 'incidents-1': 100},
        'ready': {'incidents-0': 0, 'incidents-1': 0},
        'commits': {'incidents-0': 1 if drop_first_commit else 2, 'incidents-1': 0},
        'groups': {'incidents-0': 1, 'incidents-1': 0},
        'abstentions': {'incidents-0': 1, 'incidents-1': 0},
        'busy': {'incidents-0': 0, 'incidents-1': 1},
        'has_decision': {'incidents-0': 1, 'incidents-1': 0},
        'progress': {'incidents-0': 1, 'incidents-1': 0},
        'duration_count': {'incidents-0': 1}, 'duration_sum': {'incidents-0': .125}}.items()}
    series['errors'] = [{'metric': {'worker_instance': s, 'component': 'database'}, 'value': [100, str(v)]}
                        for s, v in [('incidents-0', 0), ('incidents-1', 1)]]
    series['unknown'] = [{'metric': {'worker_instance': slot, 'business_service': s}, 'value': [100, str(v)]}
                         for slot, v in [('incidents-0', 1), ('incidents-1', 0)] for s in BUSINESS_ROUTES]
    series['duration_bucket'] = [{'metric': {'worker_instance': 'incidents-0', 'le': '10'}, 'value': [100, '1']}]
    snapshot = {'observed_at_unix': 105, 'evaluated_at_unix': 105, 'series': series}
    async def wait(name, predicate):
        assert predicate(snapshot), 'dropped/incorrect process counter must fail exact accounting'
        return snapshot
    evidence.wait = wait
    end = utc_time(store.read(refs[-1])['last_bucket_end']).timestamp()
    evidence.guarded_source = AsyncMock(return_value={
        'source': [{'metric': {'worker_instance': 'incidents-0', 'business_service': s}, 'value': [100, '1']} for s in BUSINESS_ROUTES],
        'bucket_end': points({'incidents-0': end}), 'age': points({'incidents-0': 5})})
    (evidence.output / 'before-crash-completed-progress.json').write_text(json.dumps(snapshot))
    if drop_first_commit:
        with pytest.raises(AssertionError, match='exact accounting'):
            asyncio.run(evidence.final(logs, store, accepted_refs=set(refs)))
    else:
        result = asyncio.run(evidence.final(logs, store, accepted_refs=set(refs)))
        assert result['status'] == 'PASS' and result['source_gauges_match_last_local_ingest']
        assert result['current_process_counters']['commits'] == {'incidents-0': 2}
        assert result['full_NFR10_accepted'] is False


@pytest.mark.parametrize('fail_during_export', [False, True])
def test_outage_probe_resumes_owned_children_and_restores_collector_on_failure(tmp_path, monkeypatch, fail_during_export):
    import scripts.incident_worker_evidence as native
    observer = IncidentWorkerEvidence(tmp_path / 'run', ArtifactStore(tmp_path / 'source'), 'stream', 'fixture-project',
                                      metrics_endpoint='http://127.0.0.1:4317')
    signals, calls = [], []
    child = SimpleNamespace(poll=lambda: None, send_signal=lambda s: signals.append(s))
    observer.children = [child, child, child]
    observer.consumer_id = 'fixture-consumer'
    observer.catch_up = AsyncMock()
    observer._save = lambda: None
    # The fake child needs no real PID or process control; all Docker calls mocked.
    info = {'Config': {'Labels': {'com.docker.compose.project': 'fixture-project', 'com.docker.compose.service': 'otel-collector'}},
            'State': {'Running': True}, 'NetworkSettings': {'Ports': {'4317/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '4317'}]}}}
    monkeypatch.setattr(native.subprocess, 'check_output', lambda *a, **k: json.dumps([info]).encode())
    def docker(command, **kwargs):
        calls.append(command[1]); return SimpleNamespace(returncode=0)
    monkeypatch.setattr(native.subprocess, 'run', docker)
    snapshots = iter([(10, 10), (12 if fail_during_export else 10, 10), (12, 10)])
    class Connection:
        current = None
        async def fetchval(self, sql, identity):
            if 'health_batches' in sql: self.current = next(snapshots); return self.current[0]
            return self.current[1]
    @asynccontextmanager
    async def acquire(): yield Connection()
    pool = SimpleNamespace(acquire=acquire)
    async def sleep(seconds): return None
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    with pytest.raises(AssertionError): asyncio.run(observer.metrics_outage(pool, 'owned-collector'))
    assert observer.paused == [] and signals.count(signal.SIGSTOP) == signals.count(signal.SIGCONT)
    assert calls == (['stop', 'start'] if fail_during_export else [])
