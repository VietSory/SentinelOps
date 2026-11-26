"""Score the full Alibaba calibration window with verified development models."""
import argparse
from bisect import bisect_right
import json
import math
from pathlib import Path
import shutil
import tempfile

import numpy as np
import pandas as pd
import torch
from xgboost import XGBClassifier

from scripts.audit_ltfp_graph_support import read_graph
from scripts.calibrate_ltfp_development import verify as verify_calibration
from sentinelops.ml.alibaba_training import load_config, load_minutes, service_window_arrays, split_of
from sentinelops.ml.local_evaluation import risk_curve
from sentinelops.ml.ltfp_model import load
from sentinelops.ml.models import ExceedanceRisk
from sentinelops.public_data.alibaba import file_sha256
from sentinelops.telemetry.core import stable_id


MODELS = ('slope', 'xgboost', 'ltfp_self_only', 'ltfp_graph')


def project_cached(target, minute, features, neighbors, pairs):
    """Project a complete one-hop target graph from causal feature vectors."""
    nodes = sorted({target} | neighbors.get(target, set()))
    steps = range(minute - 4, minute + 1)
    ready = [all(step in features.get(node, {}) for step in steps) for node in nodes]
    values = [[features[node][step] for step in steps] if ready[index] else [[0.0] * 6 for _ in steps]
              for index, node in enumerate(nodes)]
    mask = [[bool(ready[i] and ready[j] and (i == j or (up, down) in pairs))
             for j, down in enumerate(nodes)] for i, up in enumerate(nodes)]
    self_mask = [[bool(i == j and ready[i]) for j in range(len(nodes))] for i in range(len(nodes))]
    target_index = nodes.index(target)
    last = features.get(target, {}).get(minute)
    count = sum(mask[target_index]) - 1 if ready[target_index] else None
    return values, mask, self_mask, target_index, ready[target_index], last, count


def summarize(rows, thresholds):
    """Report matched window ranking and observed calibration flags only."""
    labelled = [row for row in rows if row['risk_label'] is not None]
    common = [row for row in labelled if all(row['scores'][model] is not None for model in MODELS)]
    output = {}
    for model in MODELS:
        metric = risk_curve([row['risk_label'] for row in common], [row['scores'][model] for row in common])
        negatives = [row for row in labelled if row['risk_label'] == 0 and row['scores'][model] is not None]
        point = thresholds[model]
        flags = sum(row['scores'][model] > point['threshold'] if model == 'slope'
                    else row['scores'][model] >= point['threshold'] for row in negatives)
        metric.update(scored_windows=sum(row['scores'][model] is not None for row in rows),
                      missing_windows=sum(row['scores'][model] is None for row in rows),
                      labelled_unavailable=sum(row['scores'][model] is None for row in labelled),
                      negative_windows=len(negatives), negative_flags=flags,
                      negative_flag_fraction=flags / len(negatives) if negatives else None,
                      threshold=point['threshold'], comparison='>' if model == 'slope' else '>=')
        output[model] = metric
    return {'windows': len(rows), 'labelled_windows': len(labelled), 'common_windows': len(common),
            'unknown_windows': len(rows) - len(labelled), 'models': output}


def run(root):
    root = Path(root)
    verify_calibration(root)
    source = json.loads((root / 'preflight/development-k16-result-v5.json').read_text())
    protocol, admission = source['protocol'], source['admission']
    if (stable_id(protocol) != admission['protocol_sha256']
            or stable_id(source['selected_records']) != protocol['selection_sha256']):
        raise ValueError('paired fit provenance differs')
    for path, digest in protocol['code_sha256'].items():
        if file_sha256(path) != digest:
            raise ValueError(f'paired fit code differs: {path}')
    fit = root / 'preflight/development-k16-fit-v5'
    receipt = json.loads((fit / 'receipt.json').read_text())
    models = {name: load(fit / name, metadata_sha256=entry['metadata_sha256'], expected_identity=entry['identity'])
              for name, entry in receipt['models'].items()}
    baseline = root / 'preflight/development-k16-xgboost-v5'
    baseline_manifest = json.loads((baseline / 'manifest.json').read_text())
    risk_model = XGBClassifier()
    risk_model.load_model(baseline / 'risk.json')
    config = load_config()
    risk = ExceedanceRisk(risk_model, config['features'])
    calibration = root / 'preflight/development-k16-calibration-v5'
    calibration_manifest = json.loads((calibration / 'manifest.json').read_text())
    reference = json.loads((calibration / 'predictions.json').read_text())
    report_path = root / 'data/fit-20261113/report.json'
    if file_sha256(report_path) != protocol['fit_report_sha256'] or stable_id(config) != protocol['config_sha256']:
        raise ValueError('pinned Alibaba data or configuration differs')
    report = json.loads(report_path.read_text())
    data, provenance = load_minutes(root / 'minutes')
    expected = {item['archive']: item for item in report['source_archives']}
    if len(provenance) != 120 or any(item != expected.get(item['archive']) for item in provenance):
        raise ValueError('complete pinned Alibaba snapshot required')
    partition, window = protocol['calibration_partition'], protocol['calibration_window']
    if partition != [180, 359] or window != [324, 352] or protocol['multiplier'] != 16.0:
        raise ValueError('unexpected development partition')
    paths = [root / 'input/callgraph' / f'CallGraph_{index}.tar.gz' for index in range(20, 24)]
    graph, graph_receipts = read_graph(paths, role='calibration')
    if graph_receipts != protocol['graph_archives'] or max(graph) >= window[0] - 4:
        raise ValueError('calibration graph differs from pinned earlier topology')
    edges = set().union(*graph.values())
    pairs = edges | {(down, up) for up, down in edges}
    neighbors = {}
    for up, down in edges:
        neighbors.setdefault(up, set()).add(down)
        neighbors.setdefault(down, set()).add(up)
    data = data[data.msname.map(split_of) == 'calibration']
    data = data[data.minute.between(*partition)]
    column = config['label']['breach_multipliers'].index(protocol['multiplier'])
    features, targets = {}, []
    for service, group in data.groupby('msname'):
        series = group.set_index('minute')[['http_rt_ms', 'http_mcr_sum']].rename(
            columns={'http_rt_ms': 'rt', 'http_mcr_sum': 'rate'}).sort_index()
        valid = series[np.isfinite(series.rt) & np.isfinite(series.rate) & (series.rt > 0) & (series.rate > 0)]
        projected = service_window_arrays(valid, config)
        features[service] = {int(minute): [float(v) for v in projected['features'][index]] + [math.log(1 / 16)]
                             for index, minute in enumerate(projected['minute']) if minute in valid.index}
        observed = sorted(group.minute.unique())
        if len(observed) < config['splits']['min_minutes_per_service']:
            continue
        labelled = service_window_arrays(series, config)
        labels = {int(minute): int(labelled['labels'][index, column])
                  for index, minute in enumerate(labelled['minute'])}
        for minute in range(window[0], window[1] + 1):
            if bisect_right(observed, minute) < config['splits']['min_minutes_per_service']:
                continue
            label = labels.get(minute, -1)
            targets.append({'record_id': f'{service}:{minute}:K16', 'service': service,
                            'minute': minute, 'risk_label': None if label == -1 else label})
    rows, vectors, vector_indices = [], [], []
    previous_threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        with torch.no_grad():
            for target in targets:
                values, mask, self_mask, index, available, last, count = project_cached(
                    target['service'], target['minute'], features, neighbors, pairs)
                scores = {'slope': max(0.0, last[1]) if last is not None else None,
                          'xgboost': None, 'ltfp_self_only': None, 'ltfp_graph': None}
                if available:
                    tensor = torch.tensor(values, dtype=torch.float32)
                    scores['ltfp_graph'] = float(torch.sigmoid(models['graph'](tensor, torch.tensor(mask)))[index])
                    scores['ltfp_self_only'] = float(torch.sigmoid(models['self_only'](tensor, torch.tensor(self_mask)))[index])
                if last is not None:
                    vectors.append(last)
                    vector_indices.append(len(rows))
                rows.append({**target, 'scores': scores, 'graph_neighbor_count': count})
    finally:
        torch.set_num_threads(previous_threads)
    probabilities = risk.predict_proba(vectors)[:, 1] if vectors else []
    for index, probability in zip(vector_indices, probabilities):
        rows[index]['scores']['xgboost'] = float(probability)
    by_id = {row['record_id']: row for row in rows}
    max_delta = 0.0
    for old in reference:
        row = by_id.get(old['record_id'])
        if row is None or row['risk_label'] != old['risk_label']:
            raise ValueError('calibration reference row or label missing')
        for model in MODELS:
            value, prior = row['scores'][model], old['scores'][model]
            if (value is None) != (prior is None):
                raise ValueError('cached projection availability differs from reference')
            if value is not None:
                max_delta = max(max_delta, abs(value - prior))
    if max_delta > 1e-6:
        raise ValueError('cached projection differs from reference inference')
    summary = summarize(rows, calibration_manifest['points'])
    output = root / 'preflight/development-k16-score-v5'
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f'development score output already exists: {output}')
    stage = Path(tempfile.mkdtemp(prefix=f'.{output.name}.', dir=output.parent))
    try:
        ledger = []
        for row in rows:
            for model in MODELS:
                score = row['scores'][model]
                ledger.append({'dataset': 'alibaba-proxy-development', 'group_id': row['service'],
                    'run_id': row['service'], 'service': row['service'], 'relative_minute': row['minute'],
                    'model_id': model, 'risk_label': row['risk_label'], 'score': score,
                    'score_missing_reason': 'incomplete-feature-sequence' if score is None else None,
                    'graph_neighbor_count': row['graph_neighbor_count'], 'clock': 'relative-minute-replay'})
        pd.DataFrame(ledger).to_parquet(stage / 'predictions.parquet', index=False)
        result = {'scope': 'development-only-not-holdout', 'activation_allowed': False,
                  'clock': 'relative-minute-replay', 'summary': summary,
                  'fit_input_sha256': receipt['input_sha256'], 'xgboost_sha256': baseline_manifest['model_sha256'],
                  'calibration_manifest_sha256': file_sha256(calibration / 'manifest.json'),
                  'graph_archives': graph_receipts, 'predictions_sha256': file_sha256(stage / 'predictions.parquet'),
                  'reference_rows_checked': len(reference), 'reference_max_abs_delta': max_delta,
                  'limitations': ['Development windows were used for calibration and are not holdout.',
                                  'Window flags are not event recall or false warnings per service-hour.',
                                  'Source availability is simulated; local latency oracle and runtime parity are unavailable.']}
        (stage / 'manifest.json').write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        if output.exists():
            raise FileExistsError(f'development score output already exists: {output}')
        stage.rename(output)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.root), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
