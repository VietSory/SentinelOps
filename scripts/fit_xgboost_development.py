"""Refit the existing XGBoost baseline on the pinned LTFP development rows."""
import argparse
from bisect import bisect_right
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform
import shutil
import tempfile

import numpy as np
from xgboost import XGBClassifier

from sentinelops.ml.alibaba_live import relative_feature_values
from sentinelops.ml.alibaba_training import load_config, load_minutes, service_window_arrays, split_of
from sentinelops.ml.ltfp_model import load
from sentinelops.ml.models import ExceedanceRisk, fit_exceedance_risk
from sentinelops.public_data.alibaba import file_sha256
from sentinelops.telemetry.core import stable_id


def train_features(data, records, config, partition, window, multiplier):
    """Recompute selected labels and features from train data through each anchor."""
    expected = {}
    for record in records:
        service, minute, label = record['service'], record['minute'], record['label']
        key = (service, minute)
        if (key in expected or split_of(service) != 'train' or type(minute) is not int
                or record['record_id'] != f'{service}:{minute}:K16' or type(label) is not int
                or label not in (0, 1) or not window[0] <= minute <= window[1]
                or minute - 68 < partition[0] or minute + 7 > partition[1]):
            raise ValueError('invalid or duplicated selected train record')
        expected[key] = record
    selected_services = {service for service, _ in expected}
    data = data[data.msname.isin(selected_services) & data.minute.between(*partition)]
    column = config['label']['breach_multipliers'].index(multiplier)
    features = {}
    for service, group in data.groupby('msname'):
        series = group.set_index('minute')[['http_rt_ms', 'http_mcr_sum']].rename(
            columns={'http_rt_ms': 'rt', 'http_mcr_sum': 'rate'}).sort_index()
        arrays = service_window_arrays(series, config)
        steps = set(arrays['minute'])
        observed = sorted(group.minute.unique())
        points = {int(row.minute): (float(row.http_rt_ms), float(row.http_mcr_sum))
                  for row in group.itertuples(index=False)}
        for index, minute in enumerate(arrays['minute']):
            key = (service, int(minute))
            if key not in expected:
                continue
            record = expected[key]
            if (int(arrays['labels'][index, column]) != record['label']
                    or bisect_right(observed, minute) < config['splits']['min_minutes_per_service']
                    or not all(minute - offset in steps for offset in range(5))):
                raise ValueError('selected record differs from the causal train cohort')
            vector = [float(value) for value in arrays['features'][index]] + [math.log(1 / multiplier)]
            projected = relative_feature_values(points, int(minute), config)
            if projected is None or not np.allclose(vector[:5], projected[:5], rtol=0, atol=1e-6):
                raise ValueError('training features differ from the serving projection')
            features[key] = vector
    if len(features) != len(expected):
        raise ValueError('one or more selected train features are unavailable')
    return np.asarray([features[(row['service'], row['minute'])] for row in records], dtype=float)


def verify_existing(output, protocol, receipt, records, values, config):
    """Verify completed bytes and replay predictions before reusing a baseline."""
    manifest = json.loads((output / 'manifest.json').read_text())
    if (manifest['scope'] != 'development-train-only' or manifest['activation_allowed'] is not False
            or manifest['selection_sha256'] != protocol['selection_sha256']
            or manifest['paired_fit_input_sha256'] != receipt['input_sha256']
            or manifest['input_features_sha256'] != stable_id(values.tolist())
            or manifest['fit_report_sha256'] != protocol['fit_report_sha256']
            or manifest['config_sha256'] != protocol['config_sha256']
            or manifest['rows'] != len(records) or manifest['positive_rows'] != sum(row['label'] for row in records)
            or manifest['estimator'] != config['risk']['estimator'] or manifest['seed'] != 42
            or file_sha256(output / 'risk.json') != manifest['model_sha256']
            or file_sha256(output / 'predictions.json') != manifest['predictions_sha256']):
        raise ValueError('completed XGBoost baseline identity differs')
    predictions = json.loads((output / 'predictions.json').read_text())
    if (len(predictions) != len(records) or any(
            row['record_id'] != record['record_id'] or row['label'] != record['label']
            for row, record in zip(predictions, records))):
        raise ValueError('completed XGBoost prediction rows differ')
    model = XGBClassifier()
    model.load_model(output / 'risk.json')
    replay = ExceedanceRisk(model, config['features']).predict_proba(values)[:, 1]
    expected = np.asarray([row['score'] for row in predictions], dtype=float)
    if not np.isfinite(expected).all() or not np.allclose(replay, expected, rtol=0, atol=1e-6):
        raise ValueError('completed XGBoost predictions cannot be reproduced')
    return manifest


def run(root):
    root = Path(root)
    source_path = root / 'preflight/development-k16-result-v5.json'
    source = json.loads(source_path.read_text())
    protocol, admission, records = source['protocol'], source['admission'], source['selected_records']
    if (source['scope'] != 'development-only-not-holdout' or source['activation_allowed'] is not False
            or protocol['scope'] != 'development-only' or protocol['multiplier'] != 16.0
            or admission['role'] != 'train' or stable_id(records) != protocol['selection_sha256']
            or stable_id(protocol) != admission['protocol_sha256']):
        raise ValueError('invalid paired LTFP development provenance')
    for path, digest in protocol['code_sha256'].items():
        if file_sha256(path) != digest:
            raise ValueError(f'paired fit code differs: {path}')
    paired = root / 'preflight/development-k16-fit-v5'
    receipt = json.loads((paired / 'receipt.json').read_text())
    if (receipt['input_sha256'] != source['fit_input_sha256']
            or receipt['admission_sha256'] != stable_id(admission)):
        raise ValueError('paired fit receipt differs from the selection')
    for name, entry in receipt['models'].items():
        if source['model_metadata_sha256'].get(name) != entry['metadata_sha256']:
            raise ValueError('paired fit model pin mismatch')
        load(paired / name, metadata_sha256=entry['metadata_sha256'], expected_identity=entry['identity'])
    if set(receipt['models']) != {'graph', 'self_only'}:
        raise ValueError('paired LTFP variants required')
    report_path = root / 'data/fit-20261113/report.json'
    if file_sha256(report_path) != protocol['fit_report_sha256']:
        raise ValueError('pinned Alibaba report differs')
    report = json.loads(report_path.read_text())
    config = load_config()
    if stable_id(config) != protocol['config_sha256'] or protocol['config_sha256'] != report['config_sha256']:
        raise ValueError('feature configuration differs from paired fit')
    data, provenance = load_minutes(root / 'minutes')
    expected = {item['archive']: item for item in report['source_archives']}
    if len(provenance) != 120 or any(item != expected.get(item['archive']) for item in provenance):
        raise ValueError('complete pinned Alibaba snapshot required')
    values = train_features(data, records, config, protocol['train_partition'],
                            protocol['train_window'], protocol['multiplier'])
    labels = np.asarray([record['label'] for record in records], dtype=int)
    output = root / 'preflight/development-k16-xgboost-v5'
    if output.exists():
        return verify_existing(output, protocol, receipt, records, values, config)
    risk = fit_exceedance_risk(values, labels, np.ones(len(records)), config['features'],
        monotone_constraints=config['risk']['monotone_constraints'], estimator=config['risk']['estimator'])
    scores = risk.predict_proba(values)[:, 1]
    if not np.isfinite(scores).all() or not ((scores >= 0) & (scores <= 1)).all():
        raise ValueError('non-finite training predictions')
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f'baseline output already exists: {output}')
    stage = Path(tempfile.mkdtemp(prefix=f'.{output.name}.', dir=output.parent))
    try:
        risk.save_model(stage / 'risk.json')
        loaded = XGBClassifier()
        loaded.load_model(stage / 'risk.json')
        replay = ExceedanceRisk(loaded, config['features']).predict_proba(values)[:, 1]
        delta = float(np.max(np.abs(scores - replay)))
        if delta > 1e-6:
            raise ValueError('baseline reload changed predictions')
        predictions = [{'record_id': record['record_id'], 'label': int(record['label']),
                        'score': float(score)} for record, score in zip(records, scores)]
        (stage / 'predictions.json').write_text(json.dumps(predictions, sort_keys=True, allow_nan=False))
        result = {'scope': 'development-train-only', 'activation_allowed': False,
                  'selection_sha256': protocol['selection_sha256'], 'paired_fit_input_sha256': receipt['input_sha256'],
                  'input_features_sha256': stable_id(values.tolist()), 'fit_report_sha256': protocol['fit_report_sha256'],
                  'config_sha256': protocol['config_sha256'], 'rows': len(records),
                  'positive_rows': int(labels.sum()), 'weighting': 'unit sample weight; baseline class balancing',
                  'estimator': config['risk']['estimator'], 'seed': 42,
                  'runtime': {'python': platform.python_version(), 'numpy': version('numpy'),
                              'xgboost-cpu': version('xgboost-cpu')},
                  'model_sha256': file_sha256(stage / 'risk.json'),
                  'predictions_sha256': file_sha256(stage / 'predictions.json'), 'reload_max_abs_delta': delta,
                  'limitations': ['Training scores are in-sample and do not measure generalization.',
                                  'Selected negatives are subsampled; no operating threshold is frozen.']}
        (stage / 'manifest.json').write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        if output.exists():
            raise FileExistsError(f'baseline output already exists: {output}')
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
