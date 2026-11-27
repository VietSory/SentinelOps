"""Verify the saved Alibaba development scoring ledger and its summary."""
import argparse
import json
import math
from pathlib import Path

import pandas as pd

from scripts.calibrate_ltfp_development import verify as verify_calibration
from scripts.score_ltfp_development import MODELS, summarize
from sentinelops.ml.alibaba_training import split_of
from sentinelops.public_data.alibaba import file_sha256


def ledger_rows(frame):
    fields = {'dataset', 'group_id', 'run_id', 'service', 'relative_minute', 'model_id',
              'risk_label', 'score', 'score_missing_reason', 'graph_neighbor_count', 'clock'}
    required = ['dataset', 'group_id', 'run_id', 'service', 'relative_minute', 'model_id', 'clock']
    if (set(frame.columns) != fields or frame[required].isna().any().any()
            or frame.duplicated(['service', 'relative_minute', 'model_id']).any()):
        raise ValueError('prediction ledger schema or unique key mismatch')
    rows = []
    for (service, minute), group in frame.groupby(['service', 'relative_minute'], sort=False):
        if (not isinstance(service, str) or split_of(service) != 'calibration'
                or minute != int(minute) or not 324 <= minute <= 352 or len(group) != len(MODELS)
                or set(group.model_id) != set(MODELS) or not (group.group_id == service).all()
                or not (group.run_id == service).all() or not (group.dataset == 'alibaba-proxy-development').all()
                or not (group.clock == 'relative-minute-replay').all()
                or group.graph_neighbor_count.nunique(dropna=False) != 1):
            raise ValueError('invalid calibration window or model cohort')
        if any(not pd.isna(value) and value not in (0, 1) for value in group.risk_label):
            raise ValueError('invalid binary calibration label')
        labels = {None if pd.isna(value) else int(value) for value in group.risk_label}
        if len(labels) != 1 or not labels <= {None, 0, 1}:
            raise ValueError('model labels differ within a calibration window')
        scores = {}
        for item in group.itertuples(index=False):
            if pd.isna(item.score):
                if item.score_missing_reason != 'incomplete-feature-sequence':
                    raise ValueError('missing prediction requires its reason')
                scores[item.model_id] = None
            else:
                if (not math.isfinite(float(item.score)) or not pd.isna(item.score_missing_reason)
                        or item.model_id != 'slope' and not 0 <= item.score <= 1):
                    raise ValueError('invalid available prediction')
                scores[item.model_id] = float(item.score)
        rows.append({'risk_label': labels.pop(), 'scores': scores})
    return rows


def verify(root):
    root = Path(root)
    calibration = root / 'preflight/development-k16-calibration-v5'
    output = root / 'preflight/development-k16-score-v5'
    verify_calibration(root)
    report = json.loads((output / 'manifest.json').read_text())
    if (report['scope'] != 'development-only-not-holdout' or report['activation_allowed'] is not False
            or file_sha256(output / 'predictions.parquet') != report['predictions_sha256']
            or file_sha256(calibration / 'manifest.json') != report['calibration_manifest_sha256']
            or report['reference_rows_checked'] != 2039 or report['reference_max_abs_delta'] > 1e-6):
        raise ValueError('development scoring provenance or reference parity mismatch')
    thresholds = json.loads((calibration / 'manifest.json').read_text())['points']
    rows = ledger_rows(pd.read_parquet(output / 'predictions.parquet'))
    if summarize(rows, thresholds) != report['summary']:
        raise ValueError('prediction ledger does not reproduce the saved summary')
    return {'status': 'verified', 'scope': report['scope'], 'windows': len(rows),
            'predictions_sha256': report['predictions_sha256'],
            'reference_rows_checked': report['reference_rows_checked'],
            'reference_max_abs_delta': report['reference_max_abs_delta'],
            'limitations': ['Recomputes summary from the pinned ledger; does not independently rerun inference.',
                            'Development window results do not establish heldout event performance.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.root), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
