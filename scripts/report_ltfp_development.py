"""Publish a checksum-backed forecast table for the Alibaba development run."""
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile

from scripts.score_ltfp_development import MODELS
from scripts.verify_ltfp_development_score import verify
from sentinelops.public_data.alibaba import file_sha256


FIELDS = ('model', 'dataset', 'clock', 'common_windows', 'common_positives', 'prevalence',
          'average_precision', 'scored_windows', 'missing_windows', 'negative_windows',
          'negative_window_flags', 'negative_window_flag_fraction', 'threshold', 'comparison',
          'events_lead_ge60', 'events_lead_ge180', 'eligible_events', 'lead_p50_seconds',
          'lead_p10_seconds', 'false_warnings_per_healthy_service_hour', 'no_breach_warned_runs',
          'no_breach_runs', 'delta_recall_vs_xgboost', 'delta_recall_ci95_low', 'delta_recall_ci95_high')


def forecast_csv(summary):
    """Keep window flags and unavailable event metrics in separate columns."""
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\n')
    writer.writeheader()
    for model in MODELS:
        item = summary['models'][model]
        writer.writerow({'model': model, 'dataset': 'alibaba-proxy-development',
            'clock': 'relative-minute-replay', 'common_windows': summary['common_windows'],
            'common_positives': item['positives'], 'prevalence': item['prevalence'],
            'average_precision': item['average_precision'], 'scored_windows': item['scored_windows'],
            'missing_windows': item['missing_windows'], 'negative_windows': item['negative_windows'],
            'negative_window_flags': item['negative_flags'],
            'negative_window_flag_fraction': item['negative_flag_fraction'],
            'threshold': item['threshold'], 'comparison': item['comparison']})
    return stream.getvalue().encode()


def write_once(path, content):
    """Publish complete bytes without replacing an existing artifact."""
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError(f'existing report differs: {path}')
    finally:
        Path(name).unlink(missing_ok=True)


def run(root):
    root = Path(root)
    verification = verify(root)
    source = root / 'preflight/development-k16-score-v5'
    result = json.loads((source / 'manifest.json').read_text())
    csv_bytes = forecast_csv(result['summary'])
    manifest = {'scope': 'development-only-not-holdout', 'activation_allowed': False,
                'dataset': 'alibaba-proxy-development', 'clock': 'relative-minute-replay',
                'score_manifest_sha256': file_sha256(source / 'manifest.json'),
                'predictions_sha256': result['predictions_sha256'],
                'forecast_csv_sha256': hashlib.sha256(csv_bytes).hexdigest(),
                'reference_rows_checked': verification['reference_rows_checked'],
                'reference_max_abs_delta': verification['reference_max_abs_delta'],
                'unavailable_event_metrics': 'independent eligible event oracle and heldout local runs are unavailable',
                'limitations': ['Window flag fraction is not false warnings per service-hour.',
                                'Calibration and scoring use development data; no holdout was opened.',
                                'Ledger verification recomputes the summary but does not rerun inference.']}
    report = root / 'report'
    report.mkdir(parents=True, exist_ok=True)
    write_once(report / 'forecast.csv', csv_bytes)
    write_once(report / 'forecast.manifest.json',
               (json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + '\n').encode())
    return {'status': 'verified-development-report', 'forecast_csv': str(report / 'forecast.csv'),
            'forecast_csv_sha256': manifest['forecast_csv_sha256'], 'windows': verification['windows']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.root), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
