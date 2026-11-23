"""WP2 acceptance (frozen): per-minute early-warning risk scorer.

Spec: docs/delivery/DEMO_UPGRADE_SPEC.md, WP2 (R2.1-R2.7, A2.1-A2.7).
"""
import asyncio
from datetime import timedelta
import hashlib
import importlib.util
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from sentinelops.contracts import utc_time
from sentinelops.ml.registry import ModelRegistry
from sentinelops.telemetry.core import FEATURE_KEYS, BUSINESS_ROUTES, timestamp
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.model_signals import collect_model_signals
from sentinelops.workers import risk

from test_phase_incident_projection import PAY, source_fixture

SERVICES = sorted(BUSINESS_ROUTES)


class FixedRisk:
    """A deterministic stand-in for the XGBoost estimator (predict_proba only)."""
    def __init__(self, value):
        self.value = value

    def predict_proba(self, rows):
        import numpy as np
        return np.array([[1 - self.value, self.value] for _ in rows])


def registry_for(stream, risk_value=.7):
    np = pytest.importorskip('numpy')
    from sentinelops.ml.models import fit_detector
    import json
    rng = np.random.default_rng(7)
    detector = fit_detector(rng.normal(size=(300, 5)), rng.normal(size=(100, 5)), FEATURE_KEYS)
    registry = ModelRegistry()
    registry.identity = 'fixture-risk-release'
    profile = json.loads(stream['identity'])['source_profile']
    for service in SERVICES:
        registry._models[service] = ({'model_version': 'fixture-v1', 'ordered_features': list(FEATURE_KEYS),
            'provenance': {'source_profile': profile}, 'risk_thresholds': {'watch': .485, 'degrading': .665}},
            detector, FixedRisk(risk_value), 'fixture://validation')
    return registry


def health(tmp_path):
    store, stream, batches, records = source_fixture(tmp_path / 'health', [{PAY}] * 6)
    return store, stream, batches


def tree_hash(root):
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob('*')):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode() + path.read_bytes())
    return digest.hexdigest()


def test_scorer_never_writes_the_health_store(tmp_path):
    store, stream, batches = health(tmp_path)
    before = tree_hash(store.root)
    scorer = risk.RiskScorer(store, ArtifactStore(tmp_path / 'scratch'), registry_for(stream))
    batch = store.read(batches[-1]['manifest_ref'])
    scorer.score(batches[-1]['manifest_ref'], now=utc_time(batch['evaluated_at']))
    assert tree_hash(store.root) == before


def test_scorer_matches_incident_model_inference(tmp_path):
    store, stream, batches = health(tmp_path)
    registry = registry_for(stream, .7)
    reference = batches[-1]['manifest_ref']
    batch = store.read(reference)
    now = utc_time(batch['evaluated_at'])
    results = risk.RiskScorer(store, ArtifactStore(tmp_path / 'scratch'), registry).score(reference, now=now)
    direct = collect_model_signals(risk.ReadThroughStore(store, ArtifactStore(tmp_path / 'direct')),
                                   store.read(batch['normalization_manifest_ref']),
                                   bucket_end=batch['bucket_end'], registry=registry, now=now)
    assert set(results) == set(SERVICES)
    for _, signal in direct:
        result = results[signal['service']]
        inference = signal['inference']
        if inference is None or inference['risk'] is None:
            assert result['state'] == 'unavailable' and result['risk_score'] is None
        else:
            assert result['state'] == inference['risk']['state'] == 'degrading'
            assert result['risk_score'] == inference['risk']['risk_score']
            assert result['anomaly_score'] == inference['detection']['anomaly_score']
            assert result['model_version'] == 'fixture-v1'
        assert result['bucket_end'] == batch['bucket_end']


START = utc_time('2026-12-01T10:00:00Z')


def run_states(states, service=PAY):
    tracker = risk.EpisodeTracker()
    return [tracker.update(service, START + timedelta(minutes=i), state) for i, state in enumerate(states)]


def test_episode_rule():
    assert [e['event'] for e in run_states(['normal', 'degrading'])] == [None, None]
    events = run_states(['watch', 'degrading'])
    assert events[1]['event'] == 'started' and events[1]['started_at'] == timestamp(START + timedelta(minutes=1))
    assert run_states(['degrading', 'degrading'])[1]['event'] == 'started'
    ended = run_states(['watch', 'degrading', 'normal', 'normal'])
    assert [e['event'] for e in ended] == [None, 'started', None, 'ended']
    assert ended[2]['active'] is True and ended[3]['active'] is False
    lost = run_states(['watch', 'degrading'] + ['unavailable'] * 5)
    assert [e['event'] for e in lost][-1] == 'ended' and all(e['event'] is None for e in lost[2:-1])
    # One open episode per service; a gap of more than one bucket is not "consecutive".
    tracker = risk.EpisodeTracker()
    tracker.update(PAY, START, 'watch')
    assert tracker.update(PAY, START + timedelta(minutes=2), 'degrading')['event'] is None
    assert tracker.update(PAY, START + timedelta(minutes=3), 'degrading')['event'] == 'started'
    assert tracker.update(PAY, START + timedelta(minutes=4), 'degrading')['event'] is None


def test_unavailable_model_never_starts_an_episode(tmp_path):
    store, stream, batches = health(tmp_path)
    reference = batches[-1]['manifest_ref']
    now = utc_time(store.read(reference)['evaluated_at'])
    results = risk.RiskScorer(store, ArtifactStore(tmp_path / 'scratch'), ModelRegistry()).score(reference, now=now)
    assert all(r['state'] == 'unavailable' and r['risk_score'] is None for r in results.values())
    assert [e['event'] for e in run_states(['unavailable'] * 8)] == [None] * 8


def metric_points(reader):
    points = {}
    for resource in reader.get_metrics_data().resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                points[metric.name] = [(dict(p.attributes), p.value) for p in metric.data.data_points]
    return points


def test_risk_metrics_exact_names_labels_and_thresholds():
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    reader = InMemoryMetricReader()
    thresholds = {service: {'watch': .485, 'degrading': .665} for service in SERVICES}
    metrics = risk.RiskMetrics(reader, thresholds=thresholds)
    metrics.publish(PAY, {'state': 'degrading', 'risk_score': .7, 'bucket_end': '2026-12-01T10:01:00Z'},
                    {'active': True, 'started_at': '2026-12-01T10:01:00Z'}, scored_at=1_700_000_000.0)
    points = metric_points(reader)
    assert set(points) == {'sentinelops_risk_score', 'sentinelops_risk_state', 'sentinelops_risk_episode_active',
                           'sentinelops_risk_episode_started_unixtime', 'sentinelops_risk_last_scored_unixtime',
                           'sentinelops_risk_threshold'}
    assert points['sentinelops_risk_score'] == [({'service': PAY}, .7)]
    states = {a['state']: v for a, v in points['sentinelops_risk_state'] if a['service'] == PAY}
    assert states == {'normal': 0, 'watch': 0, 'degrading': 1, 'unavailable': 0}
    assert points['sentinelops_risk_episode_active'] == [({'service': PAY}, 1)]
    assert points['sentinelops_risk_episode_started_unixtime'] == [
        ({'service': PAY}, utc_time('2026-12-01T10:01:00Z').timestamp())]
    assert points['sentinelops_risk_last_scored_unixtime'] == [({'service': PAY}, 1_700_000_000.0)]
    assert sorted((a['service'], a['level'], v) for a, v in points['sentinelops_risk_threshold']) == sorted(
        (s, level, value) for s in SERVICES for level, value in (('watch', .485), ('degrading', .665)))
    metrics.publish(PAY, {'state': 'normal', 'risk_score': .1, 'bucket_end': '2026-12-01T10:05:00Z'},
                    {'active': False, 'started_at': None}, scored_at=1_700_000_300.0)
    points = metric_points(reader)
    assert points['sentinelops_risk_episode_active'] == [({'service': PAY}, 0)]
    assert points.get('sentinelops_risk_episode_started_unixtime', []) == []


def test_release_thresholds_come_from_the_registry(tmp_path):
    store, stream, batches = health(tmp_path)
    assert risk.release_thresholds(registry_for(stream)) == {
        service: {'watch': .485, 'degrading': .665} for service in SERVICES}


def test_scorer_scores_each_bucket_once():
    class Pool:
        def __init__(self): self.rows = []
        def acquire(self): return self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def fetchrow(self, sql, stream):
            assert 'sentinel_control.health_batches' in sql and stream == 's' * 64
            return self.rows[-1] if self.rows else None
    scored = []
    class Scorer:
        def score(self, reference, *, now):
            scored.append(reference)
            return {s: {'state': 'normal', 'risk_score': .1, 'anomaly_score': 0., 'bucket_end': 'x',
                        'model_version': 'v'} for s in SERVICES}
    published = []
    class Metrics:
        def publish(self, service, result, episode, *, scored_at): published.append(service)
    pool = Pool()
    worker = risk.RiskWorker(pool, 's' * 64, Scorer(), risk.EpisodeTracker(), Metrics(),
                             clock=lambda: START + timedelta(seconds=40))
    async def run():
        assert await worker.poll_once() is None
        pool.rows.append({'bucket_end': START, 'manifest_ref': 'artifact://manifests/' + 'a' * 64 + '.json'})
        assert await worker.poll_once() == START
        assert await worker.poll_once() is None
        pool.rows.append({'bucket_end': START + timedelta(minutes=1), 'manifest_ref': 'artifact://manifests/' + 'b' * 64 + '.json'})
        assert await worker.poll_once() == START + timedelta(minutes=1)
    asyncio.run(run())
    assert len(scored) == 2 and len(published) == 2 * len(SERVICES)


def test_scorer_never_raises_into_the_worker_loop(tmp_path):
    class Pool:
        def acquire(self): return self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def fetchrow(self, sql, stream):
            return {'bucket_end': START, 'manifest_ref': 'artifact://manifests/' + 'a' * 64 + '.json'}
    class Broken:
        def score(self, reference, *, now): raise ValueError('corrupt artifact')
    published = []
    class Metrics:
        def publish(self, service, result, episode, *, scored_at): published.append(result['state'])
    worker = risk.RiskWorker(Pool(), 's' * 64, Broken(), risk.EpisodeTracker(), Metrics(), clock=lambda: START)
    assert asyncio.run(worker.poll_once()) == START
    assert published == ['unavailable'] * len(SERVICES)


spec = importlib.util.spec_from_file_location(
    'live_observability_wp2', Path(__file__).resolve().parents[1] / 'web/deployment/observability_api.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def api_client(upstream):
    app = FastAPI()
    async def authorize(authorization: str | None = Header(None)):
        if authorization != 'Bearer fixture-read':
            raise HTTPException(401, 'Operator required')
    api.install_routes(app, authorize, 'dev', httpx.MockTransport(upstream))
    return TestClient(app)


AUTH = {'Authorization': 'Bearer fixture-read'}


def vector(rows):
    return httpx.Response(200, json={'status': 'success', 'data': {'resultType': 'vector', 'result': rows}})


def test_observability_risk_endpoints():
    def upstream(request):
        assert request.method == 'GET' and request.url.host == 'prometheus.sentinelops-b-actions.svc.cluster.local'
        query = request.url.params['query']
        if request.url.path.endswith('query_range'):
            assert query == 'sentinelops_risk_episode_started_unixtime{service="payment-service"}'
            return httpx.Response(200, json={'status': 'success', 'data': {'resultType': 'matrix', 'result': [
                {'metric': {'service': 'payment-service'}, 'values': [[1, '1764583260'], [2, '1764583260'], [3, '1764583500']]}]}})
        if query == 'sentinelops_risk_state == 1':
            return vector([{'metric': {'service': 'payment-service', 'state': 'degrading'}, 'value': [1, '1']},
                           {'metric': {'service': 'checkout-service', 'state': 'normal'}, 'value': [1, '1']}])
        if query == 'sentinelops_risk_score':
            return vector([{'metric': {'service': 'payment-service'}, 'value': [1, '0.71']}])
        if query == 'sentinelops_risk_episode_active':
            return vector([{'metric': {'service': 'payment-service'}, 'value': [1, '1']}])
        if query == 'sentinelops_risk_episode_started_unixtime':
            return vector([{'metric': {'service': 'payment-service'}, 'value': [1, '1764583260']}])
        if query == 'sentinelops_risk_last_scored_unixtime':
            return vector([{'metric': {'service': 'payment-service'}, 'value': [1, '1764583290']}])
        if query == 'sentinelops_risk_threshold':
            return vector([{'metric': {'service': 'payment-service', 'level': 'watch'}, 'value': [1, '0.485']},
                           {'metric': {'service': 'payment-service', 'level': 'degrading'}, 'value': [1, '0.665']}])
        raise AssertionError('unexpected query ' + query)
    with api_client(upstream) as c:
        assert c.get('/v1/observability/risk').status_code == 401
        body = c.get('/v1/observability/risk', headers=AUTH).json()
        assert body['schema_version'] == 'live-risk/v1'
        payment = body['services']['payment-service']
        assert payment == {'state': 'degrading', 'score': .71, 'episode_active': True,
                           'episode_started_at': '2025-12-01T10:01:00Z', 'last_scored_at': '2025-12-01T10:01:30Z',
                           'thresholds': {'watch': .485, 'degrading': .665}}
        assert body['services']['checkout-service']['state'] == 'normal'
        assert body['services']['checkout-service']['score'] is None
        assert body['services']['inventory-service'] == {'state': None, 'score': None, 'episode_active': None,
                                                         'episode_started_at': None, 'last_scored_at': None,
                                                         'thresholds': {'watch': None, 'degrading': None}}
        episodes = c.get('/v1/observability/risk/episodes?service=payment-service&start=0&end=10', headers=AUTH).json()
        assert episodes == {'service': 'payment-service',
                            'episodes': ['2025-12-01T10:01:00Z', '2025-12-01T10:05:00Z']}
        assert c.get('/v1/observability/risk/episodes?service=evil&start=0&end=10', headers=AUTH).status_code == 422


def test_observability_risk_source_outage_is_null_not_guessed():
    with api_client(lambda request: httpx.Response(503)) as c:
        body = c.get('/v1/observability/risk', headers=AUTH).json()
        assert all(v['state'] is None and v['score'] is None for v in body['services'].values())
        assert body['unavailable'] is True
