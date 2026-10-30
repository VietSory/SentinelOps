"""Incident-worker metrics acceptance; observations are outside business inputs."""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path

import httpx

from scripts.worker_metrics_evidence import NAMES, WorkerMetricEvidence
from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import BUSINESS_ROUTES


def validate_protocol(value):
    expected = {'protocol_version': 'incident-metrics-acceptance/v2',
                'require_completed_progress_before_crash': True,
                'instances': ['incidents-0', 'incidents-1'], 'backlog_pause_seconds': 130,
                'collector_outage_seconds': 75, 'minimum_backlog_steps': 2,
                'metrics_observation_seconds': 35, 'heartbeat_fresh_seconds': 15,
                'training_corpus': False, 'actions_allowed': False, 'full_NFR10_accepted': False}
    if (value != expected or value.get('require_completed_progress_before_crash') is not True
        or any(value[k] is not False for k in ('training_corpus', 'actions_allowed', 'full_NFR10_accepted'))):
        raise ValueError('unsupported incident metrics acceptance protocol')


def expected_metrics(logs, store, *, accepted_refs=None):
    """Independent counter arithmetic on printed events and immutable states."""
    totals = {key: Counter() for key in ('commits', 'groups', 'abstentions', 'busy', 'cycles', 'duration', 'errors', 'unknown')}
    latest, seen = {}, set()
    for slot, path in zip(('incidents-0', 'incidents-1'), logs[1:], strict=True):
        for line in path.read_text().splitlines():
            if not line.startswith('{'):
                continue
            row = json.loads(line)
            if row['status'] == 'DEPENDENCY_UNAVAILABLE':
                totals['errors'][slot] += 1
            if row['status'] == 'BUSY':
                totals['busy'][slot] += 1
            if row['status'] != 'PROGRESS':
                continue
            totals['cycles'][slot] += 1
            totals['duration'][slot] += row['processing_ms'] / 1000
            for event in row['events']:
                if event['status'] == 'INGESTED':
                    ref = event['state_ref']
                    if accepted_refs is not None:
                        assert ref in accepted_refs, 'printed source state is not in the durable consumer journal'
                    assert ref not in seen, 'native source step printed twice across current processes'
                    seen.add(ref)
                    state = store.read(ref)
                    assert state['last_bucket_end'] == event['bucket_end']
                    assert set(state['service_states']) == set(BUSINESS_ROUTES)
                    totals['commits'][slot] += 1
                    totals['groups'][slot] += len(event['created_jobs'])
                    for service, signal in state['service_states'].items():
                        totals['unknown'][(slot, service)] += int(signal['availability'] == 'UNKNOWN')
                    latest[slot] = state
                if event['status'] == 'ENRICHED':
                    totals['abstentions'][slot] += int(store.read(event['result_ref'])['abstained'])
    return totals, latest


class IncidentMetricEvidence(WorkerMetricEvidence):
    names = {**NAMES, 'groups': 'sentinelops_incident_groups_total',
             'abstentions': 'sentinelops_rca_abstentions_total',
             'progress': 'sentinelops_incident_has_local_progress'}
    instance_pattern = 'incidents-[01]'
    duration_guard = 'sentinelops_incident_has_local_progress'

    async def completed_before_crash(self):
        return await self.wait('before-crash-completed-progress', lambda s:
            self.fresh(s, {'incidents-0'}) and self.values(s, 'ready').get('incidents-0') == 1
            and self.values(s, 'groups').get('incidents-0', 0) > 0
            and self.values(s, 'abstentions').get('incidents-0', 0) > 0)

    async def guarded_source(self, snapshot):
        result = {}
        async with httpx.AsyncClient(base_url='http://127.0.0.1:9090', timeout=3., trust_env=False) as client:
            for key in ('source', 'age', 'bucket_end'):
                expression = snapshot['queries'][key] + ' and on(job,environment,worker_instance) (sentinelops_worker_has_local_decision == 1)'
                response = await client.get('/api/v1/query', params={'query': expression, 'time': snapshot['evaluated_at_unix']})
                response.raise_for_status()
                body = response.json()
                assert body['status'] == 'success' and body['data']['resultType'] == 'vector'
                result[key] = body['data']['result']
        return result

    async def restarted(self):
        snapshot = await self.ready('restarted-slots', instances={'incidents-0', 'incidents-1'}, value=1)
        decisions = self.values(snapshot, 'has_decision')
        assert set(decisions) == {'incidents-0', 'incidents-1'} and set(decisions.values()) <= {0, 1}
        assert 0 in decisions.values(), 'exercise a live process without its own source step'
        guarded = await self.guarded_source(snapshot)
        assert all(decisions[p['metric']['worker_instance']] == 1 for values in guarded.values() for p in values)
        (self.output / 'restart-source-guard.json').write_text(json.dumps(
            {'status': 'PASS', 'snapshot_decisions': decisions, 'guarded_source': guarded}, indent=2) + '\n')

    async def database_unavailable(self, slots):
        snapshot = await self.ready('database-unavailable', instances=slots, value=0)
        assert all(self.values(snapshot, 'errors', component='database').get(slot, 0) > 0 for slot in slots)

    async def final(self, logs, store, *, accepted_refs):
        totals, latest = expected_metrics(logs, store, accepted_refs=accepted_refs)
        slots = {'incidents-0', 'incidents-1'}

        def matches(snapshot):
            if not self.fresh(snapshot, slots) or any(self.values(snapshot, 'ready').get(s) != 0 for s in slots):
                return False
            for key in ('commits', 'groups', 'abstentions', 'busy'):
                if any(self.values(snapshot, key).get(s, 0) != totals[key][s] for s in slots):
                    return False
            if any(self.values(snapshot, 'errors', component='database').get(s, 0) != totals['errors'][s] for s in slots):
                return False
            for s in slots:
                if self.values(snapshot, 'has_decision').get(s) != int(s in latest):
                    return False
                if self.values(snapshot, 'progress').get(s) != int(totals['cycles'][s] > 0):
                    return False
                if self.values(snapshot, 'duration_count').get(s, 0) != totals['cycles'][s]:
                    return False
                if not math.isclose(self.values(snapshot, 'duration_sum').get(s, 0), totals['duration'][s], abs_tol=1e-6):
                    return False
            actual_unknown = {(p['metric']['worker_instance'], p['metric']['business_service']): float(p['value'][1])
                              for p in snapshot['series']['unknown']}
            return all(actual_unknown.get((s, service), 0) == totals['unknown'][(s, service)]
                       for s in slots for service in BUSINESS_ROUTES)

        snapshot = await self.wait('final-process-accounting', matches)
        before_crash = json.loads((self.output / 'before-crash-completed-progress.json').read_text())
        initial_groups = initial_abstentions = 0
        for line in logs[0].read_text().splitlines():
            if line.startswith('{') and (row := json.loads(line)).get('status') == 'PROGRESS':
                for event in row['events']:
                    if event['status'] == 'INGESTED':
                        assert event['state_ref'] in accepted_refs
                        initial_groups += len(event['created_jobs'])
                    if event['status'] == 'ENRICHED':
                        initial_abstentions += int(store.read(event['result_ref'])['abstained'])
        assert initial_groups > 0 and initial_abstentions > 0
        assert self.values(before_crash, 'groups')['incidents-0'] == initial_groups
        assert self.values(before_crash, 'abstentions')['incidents-0'] == initial_abstentions
        assert sum(totals['commits'].values()) > 0 and sum(totals['busy'].values()) > 0
        assert sum(totals['unknown'].values()) > 0
        assert any(float(p['metric'].get('le', 'nan')) == 10. for p in snapshot['series']['duration_bucket'])
        guarded = await self.guarded_source(snapshot)
        expected_source = {(s, service): int(signal['availability'] == 'ELIGIBLE')
                           for s, state in latest.items() for service, signal in state['service_states'].items()}
        assert {(p['metric']['worker_instance'], p['metric']['business_service']): float(p['value'][1])
                for p in guarded['source']} == expected_source
        assert {p['metric']['worker_instance']: float(p['value'][1]) for p in guarded['bucket_end']} == {
            s: utc_time(state['last_bucket_end']).timestamp() for s, state in latest.items()}
        assert {p['metric']['worker_instance'] for p in guarded['age']} == set(latest)
        assert all(math.isfinite(float(p['value'][1])) and float(p['value'][1]) >= 0 for p in guarded['age'])
        report = {'status': 'PASS', 'mode': 'actual-incident-otlp-collector-prometheus',
                  'precrash_groups_observed': initial_groups, 'precrash_abstentions_observed': initial_abstentions,
                  'current_process_counters': {k: dict(v) for k, v in totals.items() if k != 'unknown'},
                  'unknown_by_slot_service': [{'slot': s, 'service': service, 'count': n}
                                              for (s, service), n in sorted(totals['unknown'].items())],
                  'source_gauges_match_last_local_ingest': True, 'local_progress_latency_guard': True,
                  'shutdown_readiness_zero': True, 'metrics_are_business_truth': False,
                  'full_NFR10_accepted': False, 'actions_allowed': False}
        (self.output / 'acceptance.json').write_text(json.dumps(report, indent=2) + '\n')
        return report
