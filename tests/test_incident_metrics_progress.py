from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from sentinelops.incidents.health import advance_health
from sentinelops.telemetry.core import BUSINESS_ROUTES, BucketMetrics, normalize_bucket, timestamp
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.metrics import IncidentWorkerMetrics

END = datetime(2026, 10, 28, 0, 10, tzinfo=timezone.utc)
POLICY = json.loads(Path('configs/slo_health_policy.json').read_text())


def collect(reader):
    data = reader.get_metrics_data()
    return {m.name: list(m.data.data_points) for resource in data.resource_metrics
            for scope in resource.scope_metrics for m in scope.metrics}


def ingested(store, end, *, unknown, environment='test'):
    states = {}
    for service in sorted(BUSINESS_ROUTES):
        observation = None if unknown else normalize_bucket(BucketMetrics(
            service, end, 120, 0, ((0, 0), (100, 120), (math.inf, 120)), 1.,
            end + timedelta(seconds=30), ('fixture://incident-metrics',), source_profile='native-fixture'),
            environment=environment)
        states[service] = advance_health(None, observation, service=service, environment=environment,
            source_profile='native-fixture', bucket_end=end, evaluated_at=end + timedelta(seconds=30), policy=POLICY)
    state = {'last_bucket_end': timestamp(end), 'service_states': states,
             'source': {'health_identity': {'environment': environment}}}
    return {'status': 'INGESTED', 'consumer_id': 'a' * 64, 'state_ref': store.write('manifests', state),
            'bucket_end': timestamp(end), 'created_jobs': []}


def test_every_catchup_step_contributes_unknown_counts_while_gauges_track_latest(tmp_path):
    store, reader = ArtifactStore(tmp_path), InMemoryMetricReader()
    metrics = IncidentWorkerMetrics(reader, environment='test', instance='incidents-0', clock=lambda: END.timestamp() + 90)
    try:
        events = [ingested(store, END, unknown=True), ingested(store, END + timedelta(minutes=1), unknown=False)]
        metrics.observe_result({'status': 'PROGRESS', 'consumer_id': 'a' * 64, 'processing_ms': 125., 'events': events}, store)
        values = collect(reader)
        assert values['sentinelops_worker_committed_windows'][0].value == 2
        counts = {p.attributes['business_service']: p.value for p in values['sentinelops_worker_unknown_decisions']}
        assert counts == {service: 1 for service in BUSINESS_ROUTES}
        assert {p.attributes['business_service']: p.value for p in values['sentinelops_worker_source_available']} == {service: 1 for service in BUSINESS_ROUTES}
        assert values['sentinelops_worker_bucket_end_unixtime'][0].value == (END + timedelta(minutes=1)).timestamp()
        assert values['sentinelops_worker_processing_duration'][0].count == 1
        assert values['sentinelops_worker_processing_duration'][0].sum == .125
    finally:
        metrics.close()


def test_rejected_last_step_does_not_partially_mutate_counters_gauges_or_duration(tmp_path):
    store, reader = ArtifactStore(tmp_path), InMemoryMetricReader()
    metrics = IncidentWorkerMetrics(reader, environment='test', instance='incidents-0')
    try:
        events = [ingested(store, END, unknown=True), ingested(store, END + timedelta(minutes=1), unknown=False, environment='local')]
        with pytest.raises(ValueError):
            metrics.observe_result({'status': 'PROGRESS', 'consumer_id': 'a' * 64, 'processing_ms': 125., 'events': events}, store)
        values = collect(reader)
        assert values['sentinelops_worker_committed_windows'][0].value == 0
        assert 'sentinelops_worker_processing_duration' not in values
        assert 'sentinelops_worker_bucket_end_unixtime' not in values
        assert all(p.value == 0 for p in values['sentinelops_worker_unknown_decisions'])
    finally:
        metrics.close()


def test_enrichment_only_latency_has_own_progress_guard_without_borrowed_source(tmp_path):
    store, reader = ArtifactStore(tmp_path), InMemoryMetricReader()
    metrics = IncidentWorkerMetrics(reader, environment='test', instance='incidents-0')
    try:
        cold = collect(reader)
        assert cold['sentinelops_incident_has_local_progress'][0].value == 0
        assert 'sentinelops_worker_processing_duration' not in cold
        payload = json.loads(Path('samples/rca-result.sample.json').read_text())
        ref = store.write('signals', payload)
        metrics.observe_result({'status': 'PROGRESS', 'consumer_id': 'a' * 64, 'processing_ms': 125.,
                                'events': [{'status': 'ENRICHED', 'result_ref': ref}]}, store)
        values = collect(reader)
        assert values['sentinelops_incident_has_local_progress'][0].value == 1
        assert values['sentinelops_worker_has_local_decision'][0].value == 0
        assert 'sentinelops_worker_source_available' not in values
        assert 'sentinelops_worker_bucket_age' not in values
        assert values['sentinelops_worker_committed_windows'][0].value == 0
        assert values['sentinelops_worker_processing_duration'][0].count == 1
        assert values['sentinelops_worker_processing_duration'][0].sum == .125
        assert values['sentinelops_rca_abstentions'][0].value == int(payload['abstained'])
        assert all(set(p.attributes) <= {'worker_instance', 'environment'} for p in values['sentinelops_incident_groups'])
    finally:
        metrics.close()


def test_invalid_enrichment_does_not_publish_progress_or_partial_counters(tmp_path):
    store, reader = ArtifactStore(tmp_path), InMemoryMetricReader()
    metrics = IncidentWorkerMetrics(reader, environment='test', instance='incidents-0')
    try:
        ref = store.write('signals', {'abstained': True})
        event = ingested(store, END, unknown=True)
        with pytest.raises(ValueError):
            metrics.observe_result({'status': 'PROGRESS', 'consumer_id': 'a' * 64, 'processing_ms': 125.,
                                    'events': [event, {'status': 'ENRICHED', 'result_ref': ref}]}, store)
        values = collect(reader)
        assert values['sentinelops_incident_has_local_progress'][0].value == 0
        assert values['sentinelops_worker_committed_windows'][0].value == 0
        assert 'sentinelops_worker_processing_duration' not in values
    finally:
        metrics.close()


def test_wait_busy_and_empty_progress_cannot_fabricate_latency_or_source(tmp_path):
    store, reader = ArtifactStore(tmp_path), InMemoryMetricReader()
    metrics = IncidentWorkerMetrics(reader, environment='test', instance='incidents-0')
    try:
        metrics.observe_result({'status': 'WAIT'}, store)
        metrics.observe_result({'status': 'BUSY'}, store)
        with pytest.raises(ValueError, match='nonempty events'):
            metrics.observe_result({'status': 'PROGRESS', 'events': [], 'processing_ms': 0.}, store)
        values = collect(reader)
        assert values['sentinelops_incident_has_local_progress'][0].value == 0
        assert values['sentinelops_worker_busy_polls'][0].value == 1
        assert values['sentinelops_worker_committed_windows'][0].value == 0
        assert 'sentinelops_worker_processing_duration' not in values
        assert 'sentinelops_worker_source_available' not in values
    finally:
        metrics.close()
