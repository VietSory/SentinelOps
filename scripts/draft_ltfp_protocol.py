"""Build a blocked development protocol from the existing Lane A artifacts."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

import pandas as pd

from scripts.audit_ltfp_graph_support import read_graph
from sentinelops.ml.alibaba_training import load_config
from sentinelops.ml.model_benchmark_protocol import audit_protocol
from sentinelops.public_data.alibaba import file_sha256
from sentinelops.telemetry.core import stable_id


def _bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def _records(rows, edges, role):
    neighbors, outgoing = defaultdict(set), defaultdict(set)
    for up, down in edges:
        neighbors[up].add(down)
        neighbors[down].add(up)
        outgoing[up].add((up, down))
    topologies, records, used_edges = {}, [], set()
    partition = (0, 179) if role == 'train' else (180, 359)
    for row in rows:
        service, minute = row['service'], row['minute']
        if service not in topologies:
            nodes = {service} | neighbors[service]
            local = sorted(edge for node in nodes for edge in outgoing[node] if edge[1] in nodes)
            topologies[service] = (sorted(nodes - {service}), local)
            used_edges.update(local)
        contributors, local = topologies[service]
        records.append(dict(record_id=row['record_id'], service=service, role=row['role'],
            parent_split=role, split_unit='service', group_id=service, anchor_minute=minute,
            required_start=minute-68, required_end=minute+7,
            partition_start=partition[0], partition_end=partition[1],
            contributors=[dict(service=node, role=role) for node in contributors],
            directed_edges=local))
    return records, sorted(used_edges)


def run(root, base_commit):
    root = Path(root).resolve()
    if not re.fullmatch('[0-9a-f]{40}', base_commit):
        raise ValueError('base commit must be a full Git SHA')
    output = root / 'preflight/protocol-draft-v1'
    if output.exists():
        raise ValueError('protocol draft already exists')
    from scripts.verify_ltfp_development_score import verify as verify_score
    from sentinelops.ml.ltfp_training import protocol_training

    if verify_score(root)['status'] != 'verified':
        raise ValueError('verified development scoring required')
    result = json.loads((root / 'preflight/development-k16-result-v5.json').read_text())
    paired = result['protocol']
    if (stable_id(paired) != result['admission']['protocol_sha256']
            or stable_id(result['selected_records']) != paired['selection_sha256']):
        raise ValueError('paired fit protocol or selection differs from admission')
    for path, digest in paired['code_sha256'].items():
        if file_sha256(path) != digest:
            raise ValueError('fit source changed: ' + path)
    data_path = root / 'data/fit-20261113/report.json'
    if file_sha256(data_path) != paired['fit_report_sha256']:
        raise ValueError('pinned Alibaba fit report required')
    fit_receipt = json.loads((root / 'preflight/development-k16-fit-v5/receipt.json').read_text())
    if fit_receipt['input_sha256'] != result['fit_input_sha256']:
        raise ValueError('fit input pin differs')
    train = [dict(record_id=row['record_id'], service=row['service'], minute=row['minute'], role='train')
             for row in result['selected_records']]
    calibration = json.loads((root / 'preflight/development-k16-calibration-v5/predictions.json').read_text())
    selected = {(row['service'], row['minute']) for row in calibration}
    if len(selected) != len(calibration):
        raise ValueError('duplicate calibration anchor')
    scores = pd.read_parquet(root / 'preflight/development-k16-score-v5/predictions.parquet',
                             columns=['service', 'relative_minute', 'model_id'])
    scores = scores[scores.model_id == 'slope']
    rows = [dict(record_id=f'{row.service}:{int(row.relative_minute)}:K16', service=row.service,
                 minute=int(row.relative_minute),
                 role='calibration' if (row.service, row.relative_minute) in selected else 'development')
            for row in scores.itertuples(index=False)]
    if len(rows) != 58897 or sum(row['role'] == 'calibration' for row in rows) != len(selected):
        raise ValueError('development and calibration cohort mismatch')
    graph_paths = [root / 'input/callgraph' / f'CallGraph_{index}.tar.gz' for index in range(20, 24)]
    records, graphs = [], {}
    for role, cohort in (('train', train), ('calibration', rows)):
        graph, graph_receipts = read_graph(graph_paths, role=role)
        if graph_receipts != paired['graph_archives'] or max(graph) >= min(row['minute']-4 for row in cohort):
            raise ValueError('source topology differs or is too late')
        entries, edges = _records(cohort, set().union(*graph.values()), role)
        records.extend(entries)
        graphs[role] = dict(identity=stable_id({'role': role, 'archives': graph_receipts}),
            observed_at='1970-01-01T01:12:00Z', directed_edges=edges, role=role)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.protocol-draft-', dir=output.parent))
    prefix = output.relative_to(root)
    def write(name, content):
        path = temporary / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return dict(path=str(prefix / name), sha256=hashlib.sha256(content).hexdigest())
    try:
        paths = set(paired['code_sha256']) | {
            'scripts/draft_ltfp_protocol.py', 'scripts/calibrate_ltfp_development.py',
            'scripts/score_ltfp_development.py', 'scripts/verify_ltfp_development_score.py',
            'src/sentinelops/ml/model_benchmark_protocol.py',
            'src/sentinelops/ml/model_benchmark_calibration.py',
        }
        source_files = [write('source/' + path, Path(path).read_bytes()) for path in sorted(paths)]
        source = write('source.json', _bytes({'files': source_files}))
        config = load_config()
        if stable_id(config) != paired['config_sha256']:
            raise ValueError('feature configuration differs from fitted models')
        protocol = dict(schema_version='benchmark-protocol/development-v1', scope='development',
            code=dict(commit=base_commit, source_manifest=source),
            data=dict(source='alibaba-v2022', manifest=dict(path=str(data_path.relative_to(root)),
                sha256=paired['fit_report_sha256']), splits=write('splits.json', _bytes({'records': records})),
                support=write('support.json', _bytes({'lanes': {
                    lane: {'status': 'blocked', 'scope': 'real'} for lane in ('a', 'b')}}))),
            features=dict(config=write('features.json', _bytes(config)), ordered_features=config['features'],
                          sequence_steps=5, dtype='float32'),
            graph=dict(message_policy='bidirectional-plus-self',
                train_topology=write('train-topology.json', _bytes(graphs['train'])),
                inference_topologies=[write('calibration-topology.json', _bytes(graphs['calibration']))]),
            training=protocol_training(fit_receipt),
            scoring=dict(oracle='latency-only', latency_ms=750, entry_buckets=2, horizon_seconds=300,
                step_seconds=60, feature_window_seconds=300, cutoff_policy='recorded-source-availability',
                episode_policy='workers.risk.EpisodeTracker', matching='earliest-unused-per-run-service',
                forecast_interval='[onset-300,onset)', qualified_lead_seconds=60, secondary_lead_seconds=180,
                fixed_slope={'threshold': .05, 'comparison': '>'}),
            calibration=dict(comparison='>=', budget=.01, minimum_scored_negative_windows=100,
                ties='preserve', fallback='nextafter-observed-max', cohort='eligible-scored-calibration-only'),
            reporting=dict(bootstrap_replicates=1000, seed=42, unit='whole-group', interval='percentile-linear',
                primary='delta-recall-lead-ge60', lead_secondary='paired-common-matched-events',
                required_tables=['forecast', 'detector']), checks=[])
        write('protocol.json', _bytes(protocol))
        temporary.rename(output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    audit = audit_protocol(protocol, root)
    if audit['status'] != 'blocked':
        raise ValueError('development draft unexpectedly passed all gates')
    return dict(status='blocked', protocol=str(output / 'protocol.json'),
                protocol_sha256=audit['protocol_sha256'], train_records=len(train),
                calibration_records=len(selected), development_records=len(rows)-len(selected),
                reasons=audit['reasons'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--base-commit', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.base_commit), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
