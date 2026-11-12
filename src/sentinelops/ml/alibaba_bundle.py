"""ADR-016/ADR-019 model bundle (`sentinelops-model/v3`) for Alibaba-trained detectors.

One service-agnostic detector and exceedance-risk model on `http-relative-5m/v2`. The bundle
binds the frozen config, every source archive checksum, the fitting commit and
the development report; it is never an approval or activation.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil

from sentinelops.telemetry.core import stable_id

BUNDLE_VERSION = 'sentinelops-model/v3'
ORIGIN = 'admitted-alibaba-v2022'
FEATURE_SET = 'http-relative-5m/v2'


def validate_alibaba_manifest(manifest):
    provenance = manifest.get('provenance', {})
    config = provenance.get('config', {})
    archives = provenance.get('source_archives', [])
    if (manifest.get('origin') != ORIGIN or manifest.get('feature_set_version') != FEATURE_SET
            or config.get('feature_set_version') != FEATURE_SET or config.get('frozen_before_fit') is not True
            or provenance.get('config_sha256') != stable_id(config)
            or manifest.get('ordered_features') != config.get('features')
            or not re.fullmatch(r'[0-9a-f]{40}', provenance.get('fitting_source_commit') or '')
            or not re.fullmatch(r'[0-9a-f]{64}', provenance.get('report_sha256') or '')
            or not archives or any(not re.fullmatch(r'[0-9a-f]{64}', a.get('archive_sha256') or '') for a in archives)
            or len({a['archive'] for a in archives}) != len(archives)
            or manifest.get('activation_allowed') is not False
            or not 0 < manifest.get('risk_thresholds', {}).get('watch', 0) < manifest['risk_thresholds'].get('degrading', 0) < 1
            or manifest.get('fault_shape_gate_passed') is not True
            or manifest.get('detector_features') != config.get('detector', {}).get('features')):
        raise ValueError('Alibaba bundle provenance, config or feature set differs')


def package_fit(fit_dir, output):
    """Turn one train_alibaba output into an exclusive v3 bundle directory."""
    import joblib
    from sentinelops.ml.models import Detector
    fit_dir, output = Path(fit_dir), Path(output)
    report_bytes = (fit_dir / 'report.json').read_bytes()
    report = json.loads(report_bytes)
    if (report.get('report_version') != 'alibaba-relative-development/v2' or report.get('activation_allowed') is not False
            or report.get('development_fit') is not False or not report.get('evaluation')):
        raise ValueError('not a complete ADR-019 fit (development fits never become bundles)')
    if report.get('fault_shape_gate', {}).get('passed') is not True:
        raise ValueError('the fitted models failed the synthetic fault-shape gate')
    detector = joblib.load(fit_dir / 'detector.joblib')
    if not isinstance(detector, Detector) or list(detector.ordered_features) != report['config']['features']:
        raise ValueError('fitted detector differs from the frozen feature config')
    if detector.thresholds != report['thresholds']:
        raise ValueError('fitted detector thresholds differ from the report')
    output.mkdir(parents=True, exist_ok=False)
    artifacts = {}
    for name in ('detector.joblib', 'risk.json'):
        shutil.copyfile(fit_dir / name, output / name)
        artifacts[name] = hashlib.sha256((output / name).read_bytes()).hexdigest()
    (output / 'report.json').write_bytes(report_bytes)
    provenance = {'origin': ORIGIN, 'config': report['config'], 'config_sha256': report['config_sha256'],
                  'source_archives': report['source_archives'], 'fitting_source_commit': report['fitting_source_commit'],
                  'report_sha256': hashlib.sha256(report_bytes).hexdigest(), 'counts': report['counts'],
                  'service_scope': 'service-agnostic relative features; every business route uses this bundle'}
    manifest = {'bundle_version': BUNDLE_VERSION, 'origin': ORIGIN, 'provenance': provenance,
                'feature_set_version': FEATURE_SET, 'ordered_features': list(detector.ordered_features),
                'artifacts': artifacts, 'thresholds': detector.thresholds,
                'calibration_ecdf_sha256': stable_id({'if': detector.calibration_if.tolist(), 'mad': detector.calibration_mad.tolist()}),
                'library_versions': {n: importlib.metadata.version(n) for n in ('numpy', 'scikit-learn', 'joblib', 'xgboost-cpu')},
                'activation_allowed': False, 'validation_status': 'development-evaluated', 'seed': detector.seed,
                'risk_thresholds': report['risk_thresholds'], 'fault_shape_gate_passed': True,
                'detector_features': list(detector.detector_features), 'detector_config': detector.forest.get_params(),
                'preprocessing': 'train-only RobustScaler on service-relative features',
                'baseline': 'median-MAD control; same train/calibration windows and alert budget'}
    validate_alibaba_manifest(manifest)
    manifest['model_version'] = stable_id(manifest)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False) + '\n')
    return {'bundle': str(output), 'model_version': manifest['model_version'],
            'manifest_sha256': hashlib.sha256((output / 'manifest.json').read_bytes()).hexdigest()}
