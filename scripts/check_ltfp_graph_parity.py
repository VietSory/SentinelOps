"""Bounded development replay of archived HTTP captures, never live ingestion."""
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
import argparse
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
        value = json.loads(path.read_bytes())
        if path.stem != stable_id(value):
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
    for bucket, rows in suffix.items():
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--topology', required=True, type=Path,
                        help='Pinned development topology identity, observed_at and directed_edges JSON')
    args = parser.parse_args()
    print(json.dumps(check(args.run, args.topology), sort_keys=True, indent=2))
