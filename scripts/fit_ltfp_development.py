"""Fit a reproducible LTFP pair on an Alibaba development cohort only."""
import argparse
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform
import sys

from scripts.audit_ltfp_cohort import FIT_REPORT_SHA, runtime_feature_minutes, validate_partitions
from scripts.audit_ltfp_graph_support import read_graph
from sentinelops.ml.alibaba_training import load_config, load_minutes, service_window_arrays, split_of
from sentinelops.ml.ltfp_graph import build_graph_input
from sentinelops.ml.ltfp_training import fit_pair
from sentinelops.public_data.alibaba import file_sha256
from sentinelops.telemetry.core import stable_id, timestamp


TRAIN_PARTITION = (0, 179)
TRAIN_WINDOW = (162, 172)
CALIBRATION_PARTITION = (180, 359)
CALIBRATION_WINDOW = (324, 352)
MAX_SAMPLES = 3200
MULTIPLIER = 16.0


def select_records(candidates):
    """Keep positive train windows and select negative windows by a fixed hash."""
    if len({record['record_id'] for record in candidates}) != len(candidates):
        raise ValueError('duplicate candidate record')
    if any(record['label'] not in (0, 1) for record in candidates):
        raise ValueError('only labelled training windows may be selected')
    ordered = sorted(candidates, key=lambda record: (
        hashlib.sha256(f"42:{record['record_id']}".encode()).digest(), record['record_id']))
    positives = [record for record in ordered if record['label'] == 1]
    negatives = [record for record in ordered if record['label'] == 0]
    if not positives or not negatives or len(positives) >= MAX_SAMPLES:
        raise ValueError('development fit needs both classes within the sample budget')
    return positives + negatives[:MAX_SAMPLES - len(positives)]


def run(root):
    root = Path(root)
    validate_partitions({'train': TRAIN_PARTITION, 'calibration': CALIBRATION_PARTITION},
                        {'train': TRAIN_WINDOW, 'calibration': CALIBRATION_WINDOW})
    report_path = root / 'data/fit-20261113/report.json'
    if file_sha256(report_path) != FIT_REPORT_SHA:
        raise ValueError('pinned Alibaba fit report required')
    report = json.loads(report_path.read_text())
    config = load_config()
    config_sha = stable_id(config)
    if config_sha != report['config_sha256']:
        raise ValueError('feature configuration differs from pinned fit report')
    graph_paths = [root / 'input/callgraph' / f'CallGraph_{index}.tar.gz' for index in range(20, 24)]
    graph, graph_receipts = read_graph(graph_paths)
    if max(graph) >= TRAIN_WINDOW[0] - 4:
        raise ValueError('topology was not available before the first feature sequence')
    edges = set().union(*graph.values())
    neighbors = {}
    for up, down in edges:
        neighbors.setdefault(up, set()).add(down)
        neighbors.setdefault(down, set()).add(up)
    data, provenance = load_minutes(root / 'minutes')
    expected = {item['archive']: item for item in report['source_archives']}
    if len(provenance) != 120 or any(item != expected.get(item['archive']) for item in provenance):
        raise ValueError('complete pinned Alibaba snapshot required')
    data = data[data.msname.map(split_of) == 'train']
    data = data[data.minute.between(*TRAIN_PARTITION)]
    column = config['label']['breach_multipliers'].index(MULTIPLIER)
    candidates = []
    for service, group in data.groupby('msname'):
        observed = sorted(group.minute.unique())
        if len(observed) < config['splits']['min_minutes_per_service']:
            continue
        series = group.set_index('minute')[['http_rt_ms', 'http_mcr_sum']].rename(
            columns={'http_rt_ms': 'rt', 'http_mcr_sum': 'rate'}).sort_index()
        arrays = service_window_arrays(series, config)
        steps = runtime_feature_minutes(series, config)
        for index, minute in enumerate(arrays['minute']):
            label = int(arrays['labels'][index, column])
            if (not TRAIN_WINDOW[0] <= minute <= TRAIN_WINDOW[1] or label not in (0, 1)
                    or bisect_right(observed, minute) < config['splits']['min_minutes_per_service']
                    or not all(minute - offset in steps for offset in range(5))):
                continue
            candidates.append({'record_id': f'{service}:{int(minute)}:K16',
                               'service': service, 'minute': int(minute), 'label': label})
    selected = select_records(candidates)
    needed = {record['service'] for record in selected}
    needed.update(node for service in list(needed) for node in neighbors.get(service, ()))
    observations = {node: {} for node in needed}
    rows = data[data.msname.isin(needed) & data.minute.between(TRAIN_WINDOW[0] - 68, TRAIN_WINDOW[1])]
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    for row in rows.itertuples(index=False):
        minute = int(row.minute)
        if not math.isfinite(row.http_rt_ms) or not math.isfinite(row.http_mcr_sum):
            continue
        observations[row.msname][minute] = {'rt': float(row.http_rt_ms), 'rate': float(row.http_mcr_sum),
            'raw_ref': f'alibaba-minute:{row.msname}:{minute}',
            'available_at': timestamp(epoch + timedelta(minutes=minute))}
    code_paths = ('scripts/fit_ltfp_development.py', 'scripts/audit_ltfp_cohort.py',
                  'scripts/audit_ltfp_graph_support.py', 'src/sentinelops/ml/ltfp_training.py',
                  'src/sentinelops/ml/ltfp_graph.py', 'src/sentinelops/ml/ltfp_model.py',
                  'src/sentinelops/ml/alibaba_training.py', 'src/sentinelops/ml/alibaba_live.py',
                  'src/sentinelops/public_data/alibaba.py', 'src/sentinelops/telemetry/core.py')
    protocol = {'scope': 'development-only', 'fit_report_sha256': FIT_REPORT_SHA,
                'config_sha256': config_sha, 'graph_archives': graph_receipts,
                'code_sha256': {path: file_sha256(path) for path in code_paths},
                'runtime': {'python': platform.python_version(),
                            **{name: version(name) for name in ('numpy', 'pandas', 'pyarrow', 'torch')}},
                'train_partition': TRAIN_PARTITION, 'train_window': TRAIN_WINDOW,
                'calibration_partition': CALIBRATION_PARTITION, 'calibration_window': CALIBRATION_WINDOW,
                'multiplier': MULTIPLIER, 'seed': 42, 'maximum_samples': MAX_SAMPLES,
                'selection': 'all-positive-then-seed-hash-ranked-negative',
                'availability': 'simulated-at-bucket-time',
                'selection_sha256': stable_id(selected)}
    source = {'fit_report_sha256': FIT_REPORT_SHA, 'scope': 'alibaba-train-development',
              'clock': 'relative-minute-replay', 'availability': protocol['availability'],
              'rate_unit': 'normalized_call_rate_sum',
              'breach_conditioning': 'K=16; sixth feature is log(1/16)'}
    topology_identity = {'graph_archives': graph_receipts, 'scope': 'same-split-train-graph'}
    samples = []
    for index, record in enumerate(selected, 1):
        target, minute = record['service'], record['minute']
        nodes = sorted({target} | neighbors.get(target, set()))
        node_set = set(nodes)
        local_edges = sorted([list(edge) for edge in edges if edge[0] in node_set and edge[1] in node_set])
        topology = {'identity': topology_identity, 'observed_at': timestamp(epoch + timedelta(minutes=72)),
                    'directed_edges': local_edges}
        anchor = epoch + timedelta(minutes=minute)
        projected = build_graph_input(nodes, observations, anchor=anchor, as_of=anchor, config=config,
                                      topology=topology, source_identity=source,
                                      rate_unit='normalized_call_rate_sum')
        if not projected['target_available'][nodes.index(target)]:
            raise ValueError(f'selected target has incomplete causal features: {record["record_id"]}')
        for values, validity in zip(projected['values'], projected['validity']):
            for vector, ready in zip(values, validity):
                if ready:
                    vector[5] = math.log(1 / MULTIPLIER)
        samples.append({'record_id': record['record_id'], 'graph': projected,
                        'graph_sha256': stable_id(projected),
                        'labels': {node: record['label'] if node == target else None for node in nodes},
                        'loss_mask': {node: node == target for node in nodes}})
        if index % 500 == 0:
            print(f'prepared {index}/{len(selected)} train samples', file=sys.stderr, flush=True)
    admission = {'protocol_sha256': stable_id(protocol), 'source_identity': source,
                 'config_sha256': config_sha, 'graph_sha256': stable_id(topology_identity),
                 'train_split': 'sha256-service-buckets-0-5:minutes-0-179',
                 'calibration_split': 'sha256-service-buckets-6-7:minutes-180-359', 'role': 'train'}
    output = root / 'preflight/development-k16-fit-v5'
    receipt = fit_pair(samples, admission=admission, expected_admission_sha256=stable_id(admission), output=output)
    return {'scope': 'development-only-not-holdout', 'activation_allowed': False,
            'protocol': protocol, 'admission': admission, 'fit_input_sha256': receipt['input_sha256'],
            'candidate_counts': {str(label): sum(row['label'] == label for row in candidates) for label in (0, 1)},
            'selected_counts': {str(label): sum(row['label'] == label for row in selected) for label in (0, 1)},
            'selected_records': selected, 'fit_directory': str(output),
            'model_metadata_sha256': {name: item['metadata_sha256'] for name, item in receipt['models'].items()},
            'limitations': ['The sampled fit is development evidence only.',
                            'Source availability is simulated; runtime parity remains unverified.',
                            'The holdout has not been opened.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.root), sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
