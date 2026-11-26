"""Calibrate development forecasts on one causal Alibaba window per service."""
import argparse
from bisect import bisect_right
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import shutil
import tempfile

import numpy as np
import torch
from xgboost import XGBClassifier

from scripts.audit_ltfp_graph_support import read_graph
from sentinelops.ml.alibaba_training import load_config, load_minutes, service_window_arrays, split_of
from sentinelops.ml.ltfp_graph import build_graph_input
from sentinelops.ml.ltfp_model import load, predict
from sentinelops.ml.model_benchmark_calibration import calibrate
from sentinelops.ml.models import ExceedanceRisk
from sentinelops.public_data.alibaba import file_sha256
from sentinelops.telemetry.core import stable_id, timestamp


def select_anchors(candidates):
    """Choose one feature-eligible anchor per service without using labels."""
    chosen, ids = {}, set()
    for row in candidates:
        record_id, service = row['record_id'], row['service']
        if record_id in ids:
            raise ValueError('duplicate calibration record')
        ids.add(record_id)
        rank = (hashlib.sha256(f'42:{record_id}'.encode()).digest(), record_id)
        if service not in chosen or rank < chosen[service][0]:
            chosen[service] = (rank, row)
    return [chosen[service][1] for service in sorted(chosen)]


def score_graph(projected, target, models, entries):
    """Keep baseline availability separate from five-step LTFP availability."""
    nodes = projected['node_ids']
    target_index = nodes.index(target)
    scores = {'ltfp_graph': predict(models['graph'], entries['graph']['identity'], projected,
                                     expected_graph_sha256=projected['graph_sha256'])[target]}
    only = deepcopy(projected)
    only['self_only'] = True
    only['attention_mask'] = [[int(i == j and only['target_available'][i]) for j in range(len(nodes))]
                              for i in range(len(nodes))]
    only['mask_sha256'] = stable_id({'node_ids': nodes, 'mask': only['attention_mask']})
    scores['ltfp_self_only'] = predict(models['self_only'], entries['self_only']['identity'], only,
                                       expected_graph_sha256=only['graph_sha256'])[target]
    vector = projected['values'][target_index][-1] if projected['validity'][target_index][-1] else None
    scores['slope'] = max(0.0, vector[1]) if vector is not None else None
    count = (sum(projected['attention_mask'][target_index]) - 1
             if projected['target_available'][target_index] else None)
    return scores, vector, count


def operating_points(predictions, cohort_id):
    points = {}
    for model in ('xgboost', 'ltfp_self_only', 'ltfp_graph'):
        rows = [{'record_id': row['record_id'], 'eligible': True, 'risk_label': row['risk_label'],
                 'score': row['scores'][model], 'missing_reason': row['missing_reasons'][model]}
                for row in predictions]
        points[model] = calibrate(rows, calibration_identity=cohort_id)
    eligible = [row for row in predictions if row['risk_label'] == 0]
    negatives = [row for row in eligible if row['scores']['slope'] is not None]
    points['slope'] = {'threshold': .05, 'comparison': '>', 'negative_windows': len(negatives),
                       'eligible_negative_windows': len(eligible),
                       'negative_flags': sum(row['scores']['slope'] > .05 for row in negatives),
                       'kind': 'fixed-uncalibrated-baseline'}
    return points


def verify(root):
    """Check pinned files and recompute operating points from the saved ledger."""
    root = Path(root)
    output = root / 'preflight/development-k16-calibration-v5'
    result = json.loads((output / 'manifest.json').read_text())
    if (result['scope'] != 'development-only-not-holdout' or result['activation_allowed'] is not False
            or file_sha256(output / 'predictions.json') != result['predictions_sha256']
            or file_sha256(root / 'preflight/development-k16-result-v5.json') != result['paired_result_sha256']
            or file_sha256(root / 'preflight/development-k16-fit-v5/receipt.json') != result['fit_receipt_sha256']
            or file_sha256(root / 'preflight/development-k16-xgboost-v5/manifest.json') != result['baseline_manifest_sha256']
            or file_sha256(root / 'preflight/development-k16-xgboost-v5/risk.json') != result['xgboost_sha256']
            or file_sha256('scripts/calibrate_ltfp_development.py') != result['calibration_script_sha256']
            or file_sha256('src/sentinelops/ml/model_benchmark_calibration.py') != result['calibration_core_sha256']):
        raise ValueError('development calibration artifact or source pin mismatch')
    predictions = json.loads((output / 'predictions.json').read_text())
    if (len(predictions) != result['selected_services']
            or len({row['service'] for row in predictions}) != len(predictions)
            or any(row['scores']['slope'] is not None and
                   (type(row['scores']['slope']) not in (int, float) or
                    not math.isfinite(row['scores']['slope'])) for row in predictions)):
        raise ValueError('calibration ledger does not contain one row per service')
    fit = root / 'preflight/development-k16-fit-v5'
    receipt = json.loads((fit / 'receipt.json').read_text())
    for name in ('graph', 'self_only'):
        entry = receipt['models'][name]
        load(fit / name, metadata_sha256=entry['metadata_sha256'], expected_identity=entry['identity'])
    selected = [{key: row[key] for key in ('record_id', 'service', 'minute', 'risk_label')}
                for row in predictions]
    if stable_id({'selection': selected, 'fit': result['paired_fit_input_sha256'],
                  'source': result['source_identity']}) != result['cohort_sha256']:
        raise ValueError('calibration cohort identity mismatch')
    if operating_points(predictions, result['cohort_sha256']) != result['points']:
        raise ValueError('operating points differ from the saved ledger')
    return {'status': 'verified', 'scope': result['scope'], 'selected_services': len(predictions),
            'predictions_sha256': result['predictions_sha256'],
            'limitations': ['Checks file identities and recomputes operating points from the saved ledger.',
                            'Does not rerun source projections or model inference.']}


def run(root):
    root = Path(root)
    source = json.loads((root / 'preflight/development-k16-result-v5.json').read_text())
    protocol, admission = source['protocol'], source['admission']
    if (source['scope'] != 'development-only-not-holdout' or source['activation_allowed'] is not False
            or stable_id(protocol) != admission['protocol_sha256']
            or stable_id(source['selected_records']) != protocol['selection_sha256']):
        raise ValueError('invalid paired development provenance')
    for path, digest in protocol['code_sha256'].items():
        if file_sha256(path) != digest:
            raise ValueError(f'paired fit code differs: {path}')
    for name in ('numpy', 'pandas', 'pyarrow', 'torch'):
        if version(name) != protocol['runtime'][name]:
            raise ValueError(f'paired fit runtime differs: {name}')
    fit = root / 'preflight/development-k16-fit-v5'
    receipt = json.loads((fit / 'receipt.json').read_text())
    if (receipt['input_sha256'] != source['fit_input_sha256']
            or receipt['admission_sha256'] != stable_id(admission)
            or set(receipt['models']) != {'graph', 'self_only'}):
        raise ValueError('paired fit receipt differs from admitted source')
    models = {}
    for name, entry in receipt['models'].items():
        if source['model_metadata_sha256'].get(name) != entry['metadata_sha256']:
            raise ValueError('paired model pin mismatch')
        models[name] = load(fit / name, metadata_sha256=entry['metadata_sha256'],
                            expected_identity=entry['identity'])
    baseline = root / 'preflight/development-k16-xgboost-v5'
    baseline_manifest = json.loads((baseline / 'manifest.json').read_text())
    if (baseline_manifest['selection_sha256'] != protocol['selection_sha256']
            or baseline_manifest['paired_fit_input_sha256'] != receipt['input_sha256']
            or file_sha256(baseline / 'risk.json') != baseline_manifest['model_sha256']):
        raise ValueError('XGBoost baseline differs from the paired training cohort')
    config = load_config()
    report_path = root / 'data/fit-20261113/report.json'
    if file_sha256(report_path) != protocol['fit_report_sha256']:
        raise ValueError('pinned Alibaba report differs')
    report = json.loads(report_path.read_text())
    if stable_id(config) != protocol['config_sha256'] or protocol['config_sha256'] != report['config_sha256']:
        raise ValueError('feature configuration differs from fitted models')
    data, provenance = load_minutes(root / 'minutes')
    expected = {item['archive']: item for item in report['source_archives']}
    if len(provenance) != 120 or any(item != expected.get(item['archive']) for item in provenance):
        raise ValueError('complete pinned Alibaba snapshot required')
    partition, window = protocol['calibration_partition'], protocol['calibration_window']
    if (protocol['train_partition'] != [0, 179] or partition != [180, 359]
            or window != [324, 352] or protocol['multiplier'] != 16.0):
        raise ValueError('unexpected development split or risk multiplier')
    paths = [root / 'input/callgraph' / f'CallGraph_{index}.tar.gz' for index in range(20, 24)]
    graph, graph_receipts = read_graph(paths, role='calibration')
    if graph_receipts != protocol['graph_archives'] or max(graph) >= window[0] - 4:
        raise ValueError('calibration topology differs from its pinned earlier source')
    edges = set().union(*graph.values())
    neighbors = {}
    for up, down in edges:
        neighbors.setdefault(up, set()).add(down)
        neighbors.setdefault(down, set()).add(up)
    data = data[data.msname.map(split_of) == 'calibration']
    data = data[data.minute.between(*partition)]
    column = config['label']['breach_multipliers'].index(protocol['multiplier'])
    candidates = []
    for service, group in data.groupby('msname'):
        observed = sorted(group.minute.unique())
        if len(observed) < config['splits']['min_minutes_per_service']:
            continue
        series = group.set_index('minute')[['http_rt_ms', 'http_mcr_sum']].rename(
            columns={'http_rt_ms': 'rt', 'http_mcr_sum': 'rate'}).sort_index()
        arrays = service_window_arrays(series, config)
        steps = set(arrays['minute'])
        for index, minute in enumerate(arrays['minute']):
            if (not window[0] <= minute <= window[1]
                    or bisect_right(observed, minute) < config['splits']['min_minutes_per_service']
                    or not all(minute - offset in steps for offset in range(5))):
                continue
            label = int(arrays['labels'][index, column])
            candidates.append({'record_id': f'{service}:{int(minute)}:K16', 'service': service,
                               'minute': int(minute), 'risk_label': None if label == -1 else label})
    selected = select_anchors(candidates)
    if not selected:
        raise ValueError('calibration cohort has no feature-eligible services')
    needed = {record['service'] for record in selected}
    needed.update(node for service in list(needed) for node in neighbors.get(service, ()))
    observations = {node: {} for node in needed}
    rows = data[data.msname.isin(needed) & data.minute.between(window[0] - 68, window[1])]
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    for row in rows.itertuples(index=False):
        minute = int(row.minute)
        if not math.isfinite(row.http_rt_ms) or not math.isfinite(row.http_mcr_sum):
            continue
        observations[row.msname][minute] = {'rt': float(row.http_rt_ms), 'rate': float(row.http_mcr_sum),
            'raw_ref': f'alibaba-minute:{row.msname}:{minute}',
            'available_at': timestamp(epoch + timedelta(minutes=minute))}
    topology_identity = {'graph_archives': graph_receipts, 'scope': 'same-split-calibration-graph'}
    source_identity = {'fit_report_sha256': protocol['fit_report_sha256'],
                       'scope': 'alibaba-calibration-development', 'clock': 'relative-minute-replay',
                       'availability': 'simulated-at-bucket-time', 'rate_unit': 'normalized_call_rate_sum',
                       'breach_conditioning': 'K=16; sixth feature is log(1/16)'}
    risk_model = XGBClassifier()
    risk_model.load_model(baseline / 'risk.json')
    risk = ExceedanceRisk(risk_model, config['features'])
    predictions, baseline_vectors, baseline_indices = [], [], []
    previous_threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        for record in selected:
            target, minute = record['service'], record['minute']
            nodes = sorted({target} | neighbors.get(target, set()))
            node_set = set(nodes)
            local_edges = sorted([list(edge) for edge in edges if edge[0] in node_set and edge[1] in node_set])
            topology = {'identity': topology_identity, 'observed_at': timestamp(epoch + timedelta(minutes=72)),
                        'directed_edges': local_edges}
            anchor = epoch + timedelta(minutes=minute)
            projected = build_graph_input(nodes, observations, anchor=anchor, as_of=anchor, config=config,
                                          topology=topology, source_identity=source_identity,
                                          rate_unit='normalized_call_rate_sum')
            for values, validity in zip(projected['values'], projected['validity']):
                for vector, ready in zip(values, validity):
                    if ready:
                        vector[5] = math.log(1 / protocol['multiplier'])
            scores, vector, neighbor_count = score_graph(projected, target, models, receipt['models'])
            if vector is not None:
                baseline_vectors.append(vector)
                baseline_indices.append(len(predictions))
            else:
                scores['xgboost'] = None
            reasons = {name: 'incomplete-feature-sequence' if value is None else None
                       for name, value in scores.items()}
            predictions.append({**record, 'scores': scores, 'graph_sha256': projected['graph_sha256'],
                                'mask_sha256': projected['mask_sha256'], 'input_sha256': stable_id(projected),
                                'missing_reasons': reasons,
                                'available_neighbors': neighbor_count})
    finally:
        torch.set_num_threads(previous_threads)
    if baseline_vectors:
        probabilities = risk.predict_proba(baseline_vectors)[:, 1]
        for index, probability in zip(baseline_indices, probabilities):
            predictions[index]['scores']['xgboost'] = float(probability)
            predictions[index]['missing_reasons']['xgboost'] = None
    cohort_id = stable_id({'selection': selected, 'fit': receipt['input_sha256'], 'source': source_identity})
    points = operating_points(predictions, cohort_id)
    output = root / 'preflight/development-k16-calibration-v5'
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f'calibration output already exists: {output}')
    stage = Path(tempfile.mkdtemp(prefix=f'.{output.name}.', dir=output.parent))
    try:
        (stage / 'predictions.json').write_text(json.dumps(predictions, sort_keys=True, allow_nan=False))
        result = {'scope': 'development-only-not-holdout', 'activation_allowed': False,
                  'selection': 'one-seed-hash-selected-anchor-per-service-before-label-filter', 'seed': 42,
                  'cohort_sha256': cohort_id, 'candidate_windows': len(candidates),
                  'selected_services': len(selected), 'points': points,
                  'paired_fit_input_sha256': receipt['input_sha256'],
                  'paired_result_sha256': file_sha256(root / 'preflight/development-k16-result-v5.json'),
                  'fit_receipt_sha256': file_sha256(fit / 'receipt.json'),
                  'baseline_manifest_sha256': file_sha256(baseline / 'manifest.json'),
                  'calibration_script_sha256': file_sha256('scripts/calibrate_ltfp_development.py'),
                  'calibration_core_sha256': file_sha256('src/sentinelops/ml/model_benchmark_calibration.py'),
                  'source_identity': source_identity,
                  'xgboost_sha256': baseline_manifest['model_sha256'], 'graph_archives': graph_receipts,
                  'predictions_sha256': file_sha256(stage / 'predictions.json'),
                  'limitations': ['Development only; this selection was made after pilot inspection.',
                                  'One window per service avoids within-service overlap; graph neighborhoods can overlap.',
                                  'Source availability is simulated and runtime parity remains unverified.',
                                  'No candidate or threshold is frozen for holdout.']}
        (stage / 'manifest.json').write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        if output.exists():
            raise FileExistsError(f'calibration output already exists: {output}')
        stage.rename(output)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    print(json.dumps(verify(args.root) if args.verify else run(args.root), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
