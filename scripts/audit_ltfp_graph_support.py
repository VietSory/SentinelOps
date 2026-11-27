"""Measure train-only metric support of a frozen Alibaba call graph, without reading labels."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import tarfile

import pandas as pd

from sentinelops.ml.alibaba_training import split_of
from sentinelops.public_data.alibaba import file_sha256


PILOT_EDGES_SHA = '68999a3aa4866eb517808bffe0564dc2de7f58760f802a70bba05c60d4f3e0cb'
FIT_REPORT_SHA = '58a30c257fb6e181215c600dd1385faa16f5bc15ccacb35df87db69e6838f315'
MINUTE_MS = 60_000


def checked(path, digest):
    if file_sha256(path) != digest:
        raise ValueError(f'checksum mismatch: {path.name}')


def read_graph(archives, *, role='train'):
    if role not in ('train', 'calibration'):
        raise ValueError('only train or calibration graph sources are supported')
    graph = defaultdict(set)
    receipts = {}
    selected = {}
    for path in archives:
        match = re.fullmatch(r'CallGraph_([0-9]+)\.tar\.gz', path.name)
        if not match or path.name in receipts:
            raise ValueError(f'invalid or duplicate CallGraph shard: {path.name}')
        first = int(match[1]) * 3
        digest = Path(str(path) + '.sha256').read_text().split()[0]
        checked(path, digest)
        receipts[path.name] = digest
        observed_minutes = set()
        with tarfile.open(path, 'r:gz') as archive:
            member = archive.next()
            if member is None or not member.isfile() or member.name != f'CallGraph_{match[1]}.csv':
                raise ValueError(f'unexpected archive member in {path.name}')
            reader = pd.read_csv(archive.extractfile(member), usecols=['timestamp', 'um', 'dm', 'rpctype'],
                                 chunksize=250_000)
            for rows in reader:
                minute = pd.to_numeric(rows.timestamp, errors='raise') // MINUTE_MS
                if not minute.between(first, first + 2).all():
                    raise ValueError(f'timestamp outside {path.name}')
                observed_minutes.update(int(m) for m in minute.unique())
                valid = (rows.um.str.fullmatch(r'MS_[0-9]+', na=False)
                         & rows.dm.str.fullmatch(r'MS_[0-9]+', na=False) & rows.um.ne(rows.dm)
                         & rows.rpctype.str.lower().isin(('rpc', 'http')))
                pairs = rows.loc[valid, ['um', 'dm']].assign(minute=minute[valid]).drop_duplicates()
                for name in set(pairs.um) | set(pairs.dm):
                    if name not in selected:
                        selected[name] = split_of(name) == role
                for row in pairs[pairs.um.map(selected) & pairs.dm.map(selected)].itertuples(index=False):
                    graph[int(row.minute)].add((row.um, row.dm))
        if observed_minutes != set(range(first, first + 3)):
            raise ValueError(f'missing trace minutes in {path.name}')
        for minute in observed_minutes:
            graph.setdefault(minute, set())
    return graph, receipts


def read_metrics(root, wanted):
    report = root / 'fit-20261113/report.json'
    checked(report, FIT_REPORT_SHA)
    sources = json.loads(report.read_text())['source_archives']
    metrics = defaultdict(set)
    train = {}
    wanted_timestamps = [m * MINUTE_MS for m in sorted(wanted)]
    # Read all pinned shards. A boundary minute can appear in more than one file.
    for source in sources:
        path = root / 'minutes' / source['archive'].replace('.tar.gz', '.parquet')
        checked(path, source['parquet_sha256'])
        rows = pd.read_parquet(path, columns=['timestamp', 'msname'],
                               filters=[('timestamp', 'in', wanted_timestamps)])
        for name in rows.msname.unique():
            if name not in train:
                train[name] = split_of(name) == 'train'
        for stamp, group in rows.loc[rows.msname.map(train).astype(bool)].groupby('timestamp'):
            metrics[int(stamp) // MINUTE_MS].update(group.msname)
    if set(metrics) != wanted:
        raise ValueError('train metrics missing for requested minutes')
    return metrics


def support(edges, metrics, first, last):
    total = connected = active_edges = 0
    for minute in range(first, last + 1):
        present = metrics[minute]
        active = {(up, down) for up, down in edges if up in present and down in present}
        total += len(present)
        connected += len({node for pair in active for node in pair})
        active_edges += len(active)
    return {'period': [first, last], 'metric_node_minutes': total, 'on_edge_node_minutes': connected,
            'without_observed_neighbor': total - connected, 'usable_directed_edge_minutes': active_edges}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metrics-root', required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--pilot-edges', type=Path, help='Checksum-pinned train edge export for shards 20..23.')
    source.add_argument('--callgraph', type=Path, nargs='+', help='Official raw shards with adjacent .sha256 receipts.')
    parser.add_argument('--period', type=int, nargs=2, action='append', required=True, metavar=('FIRST', 'LAST'))
    args = parser.parse_args()
    if args.pilot_edges:
        checked(args.pilot_edges, PILOT_EDGES_SHA)
        graph = {int(m): set(map(tuple, pairs)) for m, pairs in json.loads(args.pilot_edges.read_text()).items()}
        receipts = {'pilot_edges': PILOT_EDGES_SHA}
    else:
        graph, receipts = read_graph(args.callgraph)
    previous = max(graph)
    for first, last in args.period:
        if not previous < first <= last < 360:
            parser.error('each period must follow the graph and earlier periods and end before minute 360')
        previous = last
    wanted = set(graph).union(*(range(a, b + 1) for a, b in args.period))
    metrics = read_metrics(args.metrics_root, wanted)
    all_edges = set.union(*graph.values())
    coobserved = {(up, down) for m, pairs in graph.items() for up, down in pairs
                  if up in metrics[m] and down in metrics[m]}
    result = {'graph_minutes': sorted(graph), 'sources': receipts, 'fit_report_sha256': FIT_REPORT_SHA}
    for key, edges in [('coobserved', coobserved), ('trace_topology', all_edges)]:
        result[key] = {'directed_edges': len(edges), 'nodes': len({n for pair in edges for n in pair}),
                       'support': [support(edges, metrics, a, b) for a, b in args.period]}
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
