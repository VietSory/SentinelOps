"""Bounded development replay of archived HTTP captures, never live ingestion."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import argparse
import asyncio
from contextlib import asynccontextmanager, ExitStack
import hashlib
import subprocess
import tempfile
import threading
from unittest.mock import patch

import httpx
import yaml
import json
from pathlib import Path

from sentinelops.contracts import utc_time
from sentinelops.ml.alibaba_live import project_graph_window
from sentinelops.ml.alibaba_training import load_config
from sentinelops.ml.ltfp_graph import build_graph_input
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2, decode_capture, mean_latency_ms


class Replay:
    def __init__(self, root):
        self.root, self.objects = root, {}

    def read(self, ref):
        if ref in self.objects:
            return self.objects[ref]
        path = self.root / ref.removeprefix('artifact://')
        data = path.read_bytes()
        value = json.loads(data)
        if path.stem != hashlib.sha256(data).hexdigest():
            raise ValueError('archived artifact checksum differs')
        return value


def compare(store, captures, *, anchor, cutoff, identity, topology, config):
    """Build offline observations independently of the accepted-history decoder."""
    nodes = ['checkout-service', 'payment-service', 'inventory-service']
    observations = {node: {} for node in nodes}
    previous = None
    for bucket, rows in sorted(captures.items()):
        if utc_time(bucket) > anchor:
            continue
        assert len(rows) == 3 and {raw['service'] for _, raw in rows} == set(nodes)
        evaluated = max(utc_time(raw['collected_at']) for _, raw in rows)
        # Receipt is fixed by the reconstructed batch, including negative mutations.
        ref = 'batch:' + bucket
        if ref not in store.objects:
            store.objects[ref] = {'identity': identity, 'stream_id': stable_id(identity),
                'bucket_end': bucket, 'evaluated_at': timestamp(evaluated),
                'current_raw_refs': [key for key, _ in rows], 'previous_batch_ref': previous}
        batch = store.objects[ref]
        previous = ref
        for raw_ref, raw in rows:
            if utc_time(batch['evaluated_at']) > cutoff or utc_time(raw['collected_at']) > cutoff:
                continue
            observation = decode_capture(raw, environment=identity['environment'])
            mean = mean_latency_ms(raw)
            if observation['quality']['eligible_for_inference'] and mean is not None and mean > 0:
                minute = int(utc_time(bucket).timestamp()) // 60
                observations[raw['service']][minute] = {'rt': mean,
                    'rate': observation['measurements']['request_count'], 'raw_ref': raw_ref,
                    'available_at': raw['collected_at']}
    offline = build_graph_input(nodes, observations, anchor=anchor, as_of=cutoff, config=config,
                                topology=topology, source_identity=identity)
    accepted = project_graph_window(store, previous, node_ids=nodes, topology=topology,
                                    as_of=cutoff, config=config)
    assert accepted is not None
    assert accepted == offline, 'offline and reconstructed accepted graph inputs differ'
    return accepted


def check(run_path, topology_path):
    run = json.loads(run_path.read_bytes())
    store = Replay(run_path.parent / 'serving')
    source = store.read(run['source_manifest_ref'])
    captures = defaultdict(list)
    for ref in source['raw_refs']:
        raw = store.read(ref)
        captures[raw['bucket_end']].append((ref, raw))
    anchor = utc_time(run['warmup_end_at']).replace(second=0, microsecond=0) + timedelta(minutes=2)
    cutoff = max(utc_time(raw['collected_at']) for _, raw in captures[timestamp(anchor)])
    topology = json.loads(topology_path.read_bytes())
    identity = {'source_profile': SOURCE_PROFILE_V2, 'environment': run['environment'],
                'worker_version': 'continuous-health/v1'}
    config = load_config()
    options = dict(anchor=anchor, cutoff=cutoff, identity=identity, topology=topology, config=config)
    base = compare(store, captures, **options)
    assert all(base['target_available']), 'real input lacks a full T=5 graph sequence'
    late = deepcopy(captures)
    target_bucket = timestamp(anchor - timedelta(minutes=2))
    ref, raw = next(item for item in late[target_bucket] if item[1]['service'] == 'payment-service')
    raw['collected_at'] = timestamp(cutoff + timedelta(microseconds=1))
    store.objects[ref] = raw
    delayed = compare(store, late, **options)
    assert delayed['target_available'] == [True, False, True]
    assert delayed['attention_mask'] == [[1, 0, 1], [0, 0, 0], [1, 0, 1]]
    raw['collected_at'] = timestamp(cutoff)
    boundary = compare(store, late, **options)
    assert all(boundary['target_available'])
    store.objects.pop(ref)
    suffix = deepcopy(captures)
    for bucket, rows in sorted(suffix.items()):
        if utc_time(bucket) > anchor:
            for key, raw in rows:
                raw['queries'] = {}
                store.objects[key] = raw
    assert compare(store, suffix, **options) == base
    return {'scope': 'real archived HTTP; reconstructed health batches; no live ingestion or scorer',
        'run_id': run['run_id'], 'anchor': timestamp(anchor), 'as_of': timestamp(cutoff),
        'run_sha256': stable_id(run), 'source_ref': run['source_manifest_ref'],
        'topology_input_sha256': stable_id(topology),
        'topology_provenance': topology.get('provenance', 'caller-supplied; independently verify availability'),
        'node_ids': base['node_ids'], 'shape': [3, 5, 6], 'units': base['units'],
        'graph_sha256': base['graph_sha256'], 'config_sha256': base['config_sha256'],
        'input_sha256': stable_id(base), 'max_abs_error': 0.0, 'late_individual_receipt': True,
        'exact_receipt_boundary': True, 'future_suffix_invariant': True,
        'raw_refs': base['raw_refs'], 'attention_mask': base['attention_mask']}


class ReplayDB:
    """Only the HealthWorker SQL seam; no PostgreSQL integration claim."""
    def __init__(self):
        self.stream = None
        self.batches, self.signals = [], []
        self.locked = False
        self.fail_checkpoint = False

    @asynccontextmanager
    async def acquire(self, *, timeout):
        assert timeout == 10
        yield self

    def is_closed(self):
        return False

    @asynccontextmanager
    async def transaction(self):
        snapshot = deepcopy((self.stream, self.batches, self.signals))
        try:
            yield
        except BaseException:
            self.stream, self.batches, self.signals = snapshot
            raise

    async def fetchval(self, query, *args):
        if query == "SELECT pg_try_advisory_lock($1)":
            assert not self.locked
            self.locked = True
        elif query == "SELECT pg_advisory_unlock($1)":
            assert self.locked
            self.locked = False
        else:
            raise AssertionError("unexpected replay SQL")
        return True

    async def fetchrow(self, query, stream_id):
        assert query == "SELECT * FROM sentinel_control.health_streams WHERE stream_id=$1"
        assert self.stream['stream_id'] == stream_id
        return deepcopy(self.stream)

    async def fetch(self, query, stream_id, start, end=None):
        assert self.stream['stream_id'] == stream_id
        if 'SELECT * FROM sentinel_control.health_signals' in query:
            assert end is None
            return [deepcopy(row) for row in self.signals if row['bucket_end'] == start]
        assert 'SELECT manifest_ref,bucket_end FROM sentinel_control.health_batches' in query
        return [deepcopy(row) for row in self.batches if start <= row['bucket_end'] < end]

    async def execute(self, query, *args):
        query = ' '.join(query.split())
        if query.startswith('INSERT INTO sentinel_control.health_streams('):
            stream_id, identity, initial = args
            if self.stream is None:
                self.stream = dict(stream_id=stream_id, identity=identity, initial_bucket_end=initial,
                                   last_bucket_end=None, last_batch_ref=None)
            assert self.stream['stream_id'] == stream_id
            return 'INSERT 0 1'
        assert args[0] == self.stream['stream_id']
        if query.startswith('INSERT INTO sentinel_control.health_batches('):
            _, end, ref, evaluated = args
            assert not any(row['bucket_end'] == end for row in self.batches)
            self.batches.append(dict(bucket_end=end, manifest_ref=ref, evaluated_at=evaluated))
            return 'INSERT 0 1'
        if query.startswith('INSERT INTO sentinel_control.health_signals '):
            _, end, service, signal_id, ref, payload = args
            assert not any(row['bucket_end'] == end and row['service'] == service for row in self.signals)
            self.signals.append(dict(bucket_end=end, service=service, signal_id=signal_id,
                                     signal_ref=ref, payload=payload))
            return 'INSERT 0 1'
        assert query.startswith('UPDATE sentinel_control.health_streams SET last_bucket_end=')
        _, end, ref, previous = args
        if self.fail_checkpoint or self.stream['last_bucket_end'] != previous:
            return 'UPDATE 0'
        self.stream.update(last_bucket_end=end, last_batch_ref=ref)
        return 'UPDATE 1'


class WorkerReplay:
    """Actual worker methods with archived HTTP, explicit clocks and disposable fakeDB."""
    def __init__(self, root, captures, *, environment, policy):
        from sentinelops.telemetry.store import ArtifactStore
        from sentinelops.workers.health import HealthWorker
        from sentinelops.telemetry import prometheus
        self.captures, self.store, self.db = captures, ArtifactStore(root), ReplayDB()
        self.current = None
        self.local = threading.local()
        self.receipts, self.capture_calls, self.http_calls = [], [], 0
        self.worker = HealthWorker(self.db, self.store, policy, endpoint='http://127.0.0.1:9090',
            environment=environment, source_profile=SOURCE_PROFILE_V2,
            configuration_hash=stable_id({'scope': 'archived-worker-replay/v1',
                'queries': {node: prometheus.queries(node, SOURCE_PROFILE_V2) for node in NODES}}))

    def transport(self, request):
        assert str(request.url).split('?')[0] == 'http://127.0.0.1:9090/api/v1/query'
        raw = self.local.raw
        assert float(request.url.params['time']) == utc_time(raw['bucket_end']).timestamp()
        matches = [entry['response'] for entry in raw['queries'].values()
                   if entry['expression'] == request.url.params['query']]
        assert len(matches) == 1, 'unexpected archived query'
        self.http_calls += 1
        return httpx.Response(200, json=matches[0])

    async def tick(self, bucket, *, now=None):
        from sentinelops.workers import health
        from sentinelops.telemetry import prometheus
        rows = self.captures[bucket]
        self.current = now or max(utc_time(raw['collected_at']) for _, raw in rows)
        replay = self

        class WorkerClock(datetime):
            @classmethod
            def now(cls, tz=None): return replay.current

        class CaptureClock(datetime):
            @classmethod
            def now(cls, tz=None): return utc_time(replay.local.raw['collected_at'])

        native, source = health.native_capture, prometheus.PrometheusSource

        def capture(endpoint, service, end, **options):
            ref, raw = next(item for item in rows if item[1]['service'] == service)
            assert timestamp(end) == bucket
            replay.local.raw = raw
            actual = native(endpoint, service, end, **options)
            assert actual == raw, 'native capture differs from archived bytes'
            assert 'artifact://raw/' + stable_id(actual) + '.json' == ref
            replay.capture_calls.append((bucket, service, ref))
            return actual

        with ExitStack() as stack:
            stack.enter_context(patch.object(health, 'datetime', WorkerClock))
            stack.enter_context(patch.object(prometheus, 'datetime', CaptureClock))
            stack.enter_context(patch.object(health, 'native_capture', capture))
            stack.enter_context(patch.object(health, 'PrometheusSource',
                lambda endpoint: source(endpoint, transport=httpx.MockTransport(self.transport))))
            result = await self.worker.tick()
        if result['status'] == 'COMMITTED':
            batch = self.store.read(result['manifest_ref'])
            assert batch['current_raw_refs'] == sorted(ref for ref, _ in rows)
            assert batch['evaluated_at'] == timestamp(self.current)
            assert batch['previous_batch_ref'] == (self.db.batches[-2]['manifest_ref']
                                                   if len(self.db.batches) > 1 else None)
            assert len(self.db.signals) == 3 * len(self.db.batches)
            self.receipts.append(dict(bucket_end=bucket, evaluated_at=timestamp(self.current),
                                      manifest_ref=result['manifest_ref']))
        return result


NODES = ['checkout-service', 'payment-service', 'inventory-service']


def worker_compare(replay, batch_ref, captures, *, anchor, cutoff, topology, config):
    """Offline decoding uses original captures, never worker normalization or accepted decoder."""
    observations = {node: {} for node in NODES}
    offline = None
    anchor_receipt = max(utc_time(raw['collected_at']) for _, raw in captures[timestamp(anchor)])
    if anchor_receipt <= cutoff:
        for bucket, rows in sorted(captures.items()):
            if utc_time(bucket) > anchor or max(utc_time(raw['collected_at']) for _, raw in rows) > cutoff:
                continue
            for ref, raw in rows:
                decoded = decode_capture(raw, environment=replay.worker.environment)
                mean = mean_latency_ms(raw)
                if decoded['quality']['eligible_for_inference'] and mean is not None and mean > 0:
                    observations[raw['service']][int(utc_time(bucket).timestamp()) // 60] = {
                        'rt': mean, 'rate': decoded['measurements']['request_count'],
                        'raw_ref': ref, 'available_at': raw['collected_at']}
        offline = build_graph_input(NODES, observations, anchor=anchor, as_of=cutoff, config=config,
                                    topology=topology, source_identity=replay.worker.identity)
    actual = project_graph_window(replay.store, batch_ref, node_ids=NODES, topology=topology,
                                  as_of=cutoff, config=config)
    assert actual == offline, 'offline and actual worker graph inputs differ'
    return actual


async def replay_worker_cases(root, captures, *, anchor, topology, environment, policy, config):
    replay = WorkerReplay(root, captures, environment=environment, policy=policy)
    prefix = [bucket for bucket in sorted(captures) if utc_time(bucket) <= anchor]
    for index, bucket in enumerate(prefix):
        if index == 1:
            calls = len(replay.capture_calls)
            assert (await replay.tick(bucket, now=utc_time(bucket) + timedelta(seconds=29)))['status'] == 'WAIT'
            assert len(replay.capture_calls) == calls and len(replay.db.batches) == 1
        result = await replay.tick(bucket)
        assert result['status'] == 'COMMITTED'
    ref = result['manifest_ref']
    cutoff = max(utc_time(raw['collected_at']) for _, raw in captures[timestamp(anchor)])
    base = worker_compare(replay, ref, captures, anchor=anchor, cutoff=cutoff, topology=topology, config=config)
    assert all(base['target_available']), 'archive lacks a full T=5 input'
    snapshot = deepcopy((replay.db.stream, replay.db.batches, replay.db.signals))
    for bucket in sorted(captures):
        if utc_time(bucket) > anchor:
            assert (await replay.tick(bucket))['status'] == 'COMMITTED'
    assert worker_compare(replay, ref, captures, anchor=anchor, cutoff=cutoff,
                          topology=topology, config=config) == base
    original_schedule = deepcopy(replay.receipts)
    anchor_manifest = replay.store.read(ref)
    normalization = replay.store.read(anchor_manifest['normalization_manifest_ref'])
    original_calls, original_http = len(replay.capture_calls), replay.http_calls
    assert original_http == original_calls * 8
    # Branch only disposable fakeDB state; immutable artifacts remain content-addressed.
    replay.db.stream, replay.db.batches, replay.db.signals = deepcopy(snapshot)
    suffix = deepcopy(captures)
    for bucket, rows in sorted(suffix.items()):
        if utc_time(bucket) > anchor:
            for index, (old, raw) in enumerate(rows):
                sample = raw['queries']['duration_sum']['response']['data']['result'][0]['value']
                sample[1] = str(float(sample[1]) * 2)
                rows[index] = ('artifact://raw/' + stable_id(raw) + '.json', raw)
            replay.captures = suffix
            assert (await replay.tick(bucket))['status'] == 'COMMITTED'
    assert worker_compare(replay, ref, suffix, anchor=anchor, cutoff=cutoff,
                          topology=topology, config=config) == base
    late = deepcopy(captures)
    rows = late[timestamp(anchor)]
    index = next(i for i, (_, raw) in enumerate(rows) if raw['service'] == 'payment-service')
    old, raw = rows[index]
    raw['collected_at'] = timestamp(cutoff + timedelta(microseconds=1))
    new = 'artifact://raw/' + stable_id(raw) + '.json'
    assert old != new
    rows[index] = (new, raw)
    # A new disposable DB replays actual worker acceptance; no batch authored by checker.
    delayed = WorkerReplay(root, late, environment=environment, policy=policy)
    for bucket in prefix:
        result = await delayed.tick(bucket)
        assert result['status'] == 'COMMITTED'
    options = dict(anchor=anchor, topology=topology, config=config)
    assert worker_compare(delayed, result['manifest_ref'], late, cutoff=cutoff, **options) is None
    exact = worker_compare(delayed, result['manifest_ref'], late,
                           cutoff=cutoff + timedelta(microseconds=1), **options)
    assert all(exact['target_available'])
    return {'scope': 'actual HealthWorker archived HTTP replay; stateful fakeDB; no historical ledger or live latency',
        'anchor': timestamp(anchor), 'as_of': timestamp(cutoff), 'shape': [3, 5, 6],
        'source_identity': replay.worker.identity, 'topology': topology,
        'input_sha256': stable_id(base), 'graph_sha256': base['graph_sha256'],
        'config_sha256': base['config_sha256'], 'node_ids': base['node_ids'], 'units': base['units'],
        'attention_mask': base['attention_mask'], 'raw_refs': base['raw_refs'], 'max_abs_error': 0.0,
        'graph_input': base, 'anchor_manifest': anchor_manifest, 'anchor_normalization': normalization,
        'original_native_captures': original_calls, 'original_http_requests': original_http,
        'replay_clock_and_manifests': original_schedule, 'anchor_manifest_ref': ref,
        'late_raw_mapping': {'original_ref': old, 'replay_ref': new, 'collected_at': raw['collected_at']},
        'late_anchor_manifest_ref': result['manifest_ref'],
        'late_anchor_manifest': delayed.store.read(result['manifest_ref']), 'late_batch_unavailable': True,
        'exact_receipt_available': True, 'watermark_wait': True,
        'original_and_perturbed_suffix_invariant': True}


def check_worker(run_path):
    run_bytes = run_path.read_bytes()
    run = json.loads(run_bytes)
    assert run['source_commit'] == '202650d81a642068c352246791c5bafa6f2c6228'
    store = Replay(run_path.parent / 'serving')
    source = store.read(run['source_manifest_ref'])
    assert len(source['raw_refs']) <= 3 * 70
    captures = defaultdict(list)
    for ref in source['raw_refs']:
        raw = store.read(ref)
        captures[raw['bucket_end']].append((ref, raw))
    assert len(captures) <= 70
    for bucket, rows in captures.items():
        assert len(rows) == 3 and {raw['service'] for _, raw in rows} == set(NODES)
        assert all(30 <= (utc_time(raw['collected_at']) - utc_time(bucket)).total_seconds() <= 60
                   for _, raw in rows)
    minutes = sorted(utc_time(bucket) for bucket in captures)
    assert all(b - a == timedelta(minutes=1) for a, b in zip(minutes, minutes[1:]))
    topology_bytes = subprocess.check_output(['git', 'show', run['source_commit'] + ':configs/topology.yaml'])
    topology_sha = hashlib.sha256(topology_bytes).hexdigest()
    assert topology_sha == 'd1265592881a04d07bb6ed992baa59d43610c90cd76cb3565cc75dd53d0df9a3'
    edges = [[up, down] for up, targets in yaml.safe_load(topology_bytes)['dependencies'].items() for down in targets]
    topology = {'identity': {'source_commit': run['source_commit'], 'blob':
        'f96d95847e061dde9679e26250db9618a06518ec', 'sha256': topology_sha,
        'scope': 'source-pinned declared graph; availability assigned at replay setup'},
        'observed_at': run['started_at'],
        'directed_edges': [edge for edge in edges if all(node in NODES for node in edge)]}
    anchor = utc_time(run['warmup_end_at']).replace(second=0, microsecond=0) + timedelta(minutes=2)
    with tempfile.TemporaryDirectory(prefix='sentinelops-worker-parity-') as root:
        result = asyncio.run(replay_worker_cases(root, captures, anchor=anchor, topology=topology,
            environment=run['environment'], policy=json.loads(Path('configs/slo_health_policy.json').read_bytes()),
            config=load_config()))
    result.update(run_id=run['run_id'], run_sha256=hashlib.sha256(run_bytes).hexdigest(),
                  source_ref=run['source_manifest_ref'], raw_count=len(source['raw_refs']),
                  minute_count=len(captures), excluded_topology_edges=[edge for edge in edges if edge not in topology['directed_edges']])
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--worker', action='store_true', help='Run actual worker with archived HTTP/fakeDB')
    parser.add_argument('--topology', type=Path,
                        help='Pinned development topology identity, observed_at and directed_edges JSON')
    args = parser.parse_args()
    if not args.worker and args.topology is None:
        parser.error('--topology required for reconstructed adapter mode')
    print(json.dumps(check_worker(args.run) if args.worker else check(args.run, args.topology),
                     sort_keys=True, indent=2))
