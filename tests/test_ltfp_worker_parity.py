"""Software checks for the actual-worker replay harness, not live ingestion evidence."""
import asyncio
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
pytest.importorskip("pandas")

import scripts.check_ltfp_graph_parity as checker
from sentinelops.ml.alibaba_training import load_config
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2
from sentinelops.workers import health
from test_health_source import capture
from test_ltfp_graph import ANCHOR

POLICY = json.loads(Path('configs/slo_health_policy.json').read_bytes())


def captures():
    rows = defaultdict(list)
    # Thirty-five original minutes cover the real archive's same warmup span.
    for i in range(37):
        end = ANCHOR - timedelta(minutes=34-i)
        for service in checker.NODES:
            raw = capture(service, end, profile=SOURCE_PROFILE_V2)
            rows[timestamp(end)].append(('artifact://raw/' + stable_id(raw) + '.json', raw))
    # Real manifests list hash-sorted refs, not chronological buckets.
    return dict(reversed(list(rows.items())))


def topology():
    return {'identity': 'software-replay-topology',
            'observed_at': timestamp(ANCHOR-timedelta(minutes=35)),
            'directed_edges': [['checkout-service', 'payment-service'],
                               ['checkout-service', 'inventory-service']]}


def test_actual_worker_native_normalization_commits_and_causal_boundaries(tmp_path, monkeypatch):
    native = Mock(wraps=health.native_capture)
    normalize = Mock(wraps=health.process_captures)
    monkeypatch.setattr(health, 'native_capture', native)
    monkeypatch.setattr(health, 'process_captures', normalize)
    rows = captures()
    original = deepcopy(rows)
    result = asyncio.run(checker.replay_worker_cases(tmp_path, rows, anchor=ANCHOR,
        topology=topology(), environment='fixture', policy=POLICY, config=load_config()))
    assert rows == original
    assert result['shape'] == [3, 5, 6] and result['max_abs_error'] == 0
    assert result['original_native_captures'] == 111
    assert result['original_http_requests'] == 888
    assert len(result['replay_clock_and_manifests']) == 37
    assert native.call_count == (37+2+35)*3
    assert normalize.call_count == 37+2+35
    assert result['late_raw_mapping']['original_ref'] != result['late_raw_mapping']['replay_ref']
    assert result['late_batch_unavailable'] and result['exact_receipt_available']
    assert result['watermark_wait'] and result['original_and_perturbed_suffix_invariant']
    for receipt in result['replay_clock_and_manifests']:
        manifest = checker.Replay(tmp_path).read(receipt['manifest_ref'])
        assert manifest['normalization_manifest_ref'].startswith('artifact://manifests/')
        assert manifest['source_status'] == 'queried' and len(manifest['signal_refs']) == 3


def test_checkpoint_failure_rolls_back_and_retry_runs_full_tick(tmp_path):
    rows = captures()
    bucket = min(rows)
    replay = checker.WorkerReplay(tmp_path, rows, environment='fixture', policy=POLICY)

    async def exercise():
        replay.db.fail_checkpoint = True
        with pytest.raises(ValueError, match='checkpoint compare-and-set failed'):
            await replay.tick(bucket)
        assert not replay.db.batches and not replay.db.signals
        assert replay.db.stream['last_batch_ref'] is None and not replay.db.locked
        replay.db.fail_checkpoint = False
        first = await replay.tick(bucket)
        second = await replay.tick(timestamp(checker.utc_time(bucket)+timedelta(minutes=1)))
        assert replay.store.read(second['manifest_ref'])['previous_batch_ref'] == first['manifest_ref']
        assert len(replay.db.batches) == 2 and len(replay.db.signals) == 6
    asyncio.run(exercise())


def test_worker_compare_detects_drift_without_using_normalized_offline_data(tmp_path, monkeypatch):
    rows = captures()
    replay = checker.WorkerReplay(tmp_path, rows, environment='fixture', policy=POLICY)

    async def prefix():
        for bucket in sorted(rows):
            if checker.utc_time(bucket) <= ANCHOR:
                result = await replay.tick(bucket)
        return result['manifest_ref']
    ref = asyncio.run(prefix())
    original = checker.project_graph_window

    def drift(*args, **kwargs):
        graph = deepcopy(original(*args, **kwargs))
        graph['values'][0][0][0] += .1
        return graph
    monkeypatch.setattr(checker, 'project_graph_window', drift)
    with pytest.raises(AssertionError, match='actual worker graph inputs differ'):
        checker.worker_compare(replay, ref, rows, anchor=ANCHOR,
            cutoff=ANCHOR+timedelta(seconds=30), topology=topology(), config=load_config())


def test_http_transport_rejects_foreign_query_and_fake_db_unknown_sql(tmp_path):
    import httpx
    rows = captures()
    replay = checker.WorkerReplay(tmp_path, rows, environment='fixture', policy=POLICY)
    raw = rows[min(rows)][0][1]
    replay.local.raw = raw
    with pytest.raises(AssertionError, match='unexpected archived query'):
        replay.transport(httpx.Request('GET', 'http://127.0.0.1:9090/api/v1/query',
            params={'time': checker.utc_time(raw['bucket_end']).timestamp(), 'query': 'foreign'}))
    with pytest.raises(AssertionError, match='unexpected replay SQL'):
        asyncio.run(replay.db.fetchval('DELETE FROM arbitrary'))
