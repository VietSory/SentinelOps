"""Audit Alibaba LTFP cohort support after service and time partitioning."""
import argparse
from bisect import bisect_right
from collections import defaultdict
import json
from pathlib import Path

import numpy as np

from sentinelops.ml.alibaba_training import load_config, load_minutes, service_window_arrays, split_of
from sentinelops.public_data.alibaba import file_sha256
from sentinelops.telemetry.core import stable_id
from scripts.audit_ltfp_graph_support import read_graph as read_directed_graph


FIT_REPORT_SHA = '58a30c257fb6e181215c600dd1385faa16f5bc15ccacb35df87db69e6838f315'
ROLES = ('train', 'calibration')


def runtime_feature_minutes(series, config):
    """Match graph input filtering and require an observation at each anchor."""
    valid = series[np.isfinite(series.rt) & np.isfinite(series.rate) & (series.rt > 0) & (series.rate > 0)]
    arrays = service_window_arrays(valid, config)
    return set(arrays['minute']) & set(valid.index)


def read_graph(paths, first_step):
    """Read only same-role edges available before every requested sequence."""
    for path in paths:
        index = int(path.name.removeprefix('CallGraph_').removesuffix('.tar.gz'))
        if path.name != f'CallGraph_{index}.tar.gz' or index * 3 + 2 >= first_step:
            raise ValueError('graph shard is invalid or unavailable before feature sequence')
    neighbors = {role: defaultdict(set) for role in ROLES}
    receipts = {}
    for role in ROLES:
        graph, receipts = read_directed_graph(paths, role=role)
        for edges in graph.values():
            for caller, callee in edges:
                neighbors[role][caller].add(callee)
                neighbors[role][callee].add(caller)
    return neighbors, receipts


def validate_partitions(partitions, windows):
    """Check disjoint inclusive partitions and all sequence/outcome dependencies."""
    if set(partitions) != set(ROLES) or set(windows) != set(ROLES):
        raise ValueError('train and calibration partitions and windows are required')
    for role in ROLES:
        start, end = partitions[role]
        first, last = windows[role]
        if start > end or first > last or first - 68 < start or last + 7 > end:
            raise ValueError(f'{role} window crosses its dependency partition')
    train, calibration = partitions['train'], partitions['calibration']
    if max(train[0], calibration[0]) <= min(train[1], calibration[1]):
        raise ValueError('train and calibration time partitions overlap')


def audit(root, graph_paths, partitions, windows, multiplier=16.0):
    validate_partitions(partitions, windows)
    root = Path(root)
    graph, graph_receipts = read_graph(graph_paths, min(first for first, _ in windows.values()) - 4)
    report_path = root / 'data/fit-20261113/report.json'
    if file_sha256(report_path) != FIT_REPORT_SHA:
        raise ValueError('fit report checksum mismatch')
    report = json.loads(report_path.read_text())
    expected = {item['archive']: item for item in report['source_archives']}
    data, provenance = load_minutes(root / 'minutes')
    if len(provenance) != 120 or any(item != expected.get(item['archive']) for item in provenance):
        raise ValueError('complete pinned baseline aggregates are required')
    config = load_config()
    if stable_id(config) != report['config_sha256']:
        raise ValueError('feature configuration differs from pinned fit report')
    column = config['label']['breach_multipliers'].index(multiplier)
    data['role'] = data.msname.map(split_of)
    data = data[data.role.isin(ROLES)]
    records, available = defaultdict(list), defaultdict(set)
    excluded = {role: 0 for role in ROLES}
    for service, group in data.groupby('msname'):
        role = group.role.iloc[0]
        start, end = partitions[role]
        group = group[group.minute.between(start, end)]
        if group.minute.nunique() < config['splits']['min_minutes_per_service']:
            excluded[role] += 1
            continue
        series = group.set_index('minute')[['http_rt_ms', 'http_mcr_sum']].rename(
            columns={'http_rt_ms': 'rt', 'http_mcr_sum': 'rate'}).sort_index()
        arrays = service_window_arrays(series, config)
        steps = runtime_feature_minutes(series, config)
        observed = sorted(group.minute.unique())
        first, last = windows[role]
        for index, minute in enumerate(arrays['minute']):
            if (not first <= minute <= last
                    or bisect_right(observed, minute) < config['splits']['min_minutes_per_service']
                    or not all(minute - offset in steps for offset in range(5))):
                continue
            minute = int(minute)
            available[(role, minute)].add(service)
            onset = arrays['onsets'][index, column]
            records[(role, minute)].append((service, int(arrays['labels'][index, column]),
                                            None if np.isnan(onset) else int(onset)))
    results = []
    for role in ROLES:
        first, last = windows[role]
        counts = {'positive': 0, 'negative': 0, 'unknown': 0, 'positive_with_neighbor': 0,
                  'negative_with_neighbor': 0, 'services': set(), 'positive_events': set(),
                  'positive_services': set(), 'negative_services': set(),
                  'positive_services_with_neighbor': set(), 'negative_services_with_neighbor': set(),
                  'positive_events_with_neighbor': set()}
        for minute in range(first, last + 1):
            for service, label, onset in records[(role, minute)]:
                has_neighbor = bool(graph[role].get(service, set()) & available[(role, minute)])
                counts['services'].add(service)
                kind = {1: 'positive', 0: 'negative', -1: 'unknown'}[label]
                counts[kind] += 1
                if has_neighbor and label in (0, 1):
                    counts[kind + '_with_neighbor'] += 1
                    counts[kind + '_services_with_neighbor'].add(service)
                if label in (0, 1):
                    counts[kind + '_services'].add(service)
                if label == 1:
                    counts['positive_events'].add((service, onset))
                    if has_neighbor:
                        counts['positive_events_with_neighbor'].add((service, onset))
        results.append({'role': role, 'partition': list(partitions[role]), 'window': [first, last],
                        'excluded_services_insufficient_partition_minutes': excluded[role],
                        **{key: len(value) if isinstance(value, set) else value
                           for key, value in counts.items()}})
    return {'scope': 'exploratory-pinned-baseline-time-partitioned', 'multiplier': multiplier,
            'fit_report_sha256': FIT_REPORT_SHA, 'graph_archives': graph_receipts,
            'source_archives': provenance, 'results': results,
            'limitations': ['Service eligibility uses only observations within its time partition through the anchor.',
                            'Metric arrival time and runtime parity are not certified.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--graph', required=True, nargs='+', type=Path)
    parser.add_argument('--train-partition', required=True, nargs=2, type=int)
    parser.add_argument('--calibration-partition', required=True, nargs=2, type=int)
    parser.add_argument('--train-window', required=True, nargs=2, type=int)
    parser.add_argument('--calibration-window', required=True, nargs=2, type=int)
    args = parser.parse_args()
    partitions = {'train': args.train_partition, 'calibration': args.calibration_partition}
    windows = {'train': args.train_window, 'calibration': args.calibration_window}
    print(json.dumps(audit(args.root, args.graph, partitions, windows), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
