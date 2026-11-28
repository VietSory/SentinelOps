"""Pin both verified development candidates before preparing holdout capture."""
import hashlib
import json
import math
from pathlib import Path

from scripts.verify_ltfp_development_stage import verify_stage
from sentinelops.ml.model_benchmark_protocol import LANE_B_CANDIDATE, _bytes, _path, publish_freeze, verify_protocol
from sentinelops.public_data.alibaba import file_sha256
from sentinelops.telemetry.core import stable_id


def _lane_a(root, protocol, evidence_path):
    from sentinelops.ml.ltfp_training import protocol_training, repeated_fit_identity

    stage = verify_stage('calibrate', root, protocol, evidence_path)
    preflight = root / 'preflight'
    fit_dir = preflight / 'development-k16-fit-v5'
    replay = preflight / 'independent-replay-v5'
    original = json.loads((fit_dir / 'receipt.json').read_bytes())
    settings = original['settings']
    training = protocol['training']
    if protocol_training(original) != training or settings['dtype'] != protocol['features']['dtype']:
        raise ValueError('Lane A fitted settings differ from the frozen protocol')
    repeated_fit = replay / fit_dir.name
    repeated = json.loads((repeated_fit / 'receipt.json').read_bytes())
    if (original['input_sha256'] != repeated['input_sha256'] or original['settings'] != repeated['settings']
            or original['training_records'] != repeated['training_records']):
        raise ValueError('Lane A repeated fit training identity differs')
    if repeated_fit_identity(original) != repeated_fit_identity(repeated):
        raise ValueError('Lane A repeated fit differs from the verified model')
    for name in original['models']:
        if any(file_sha256(fit_dir / name / filename) != file_sha256(repeated_fit / name / filename)
               for filename in ('metadata.json', 'weights.pt')):
            raise ValueError('Lane A repeated fit differs from the verified model')
    cal = 'development-k16-calibration-v5/predictions.json'
    if file_sha256(preflight / cal) != file_sha256(replay / cal):
        raise ValueError('Lane A repeated calibration predictions differ')
    evidence = json.loads(evidence_path.read_bytes())
    return {'status': stage['status'], 'fit_sha256': stage['fit_receipt_sha256'],
            'ltfp_metadata_sha256': evidence['fit_metadata_sha256'],
            'xgboost_sha256': evidence['xgboost_model_sha256'],
            'calibration_sha256': evidence['calibration']['manifest_sha256'],
            'thresholds': stage['thresholds'], 'slope_comparison': '>',
            'recovery': 'completed-fit-and-independent-calibration-replay'}


def _pinned_fit_lacks_combo(path, detector_sha256):
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != detector_sha256:
        return False
    try:
        artifact = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    calibration = artifact.get('calibration') if isinstance(artifact, dict) else None
    return isinstance(calibration, dict) and calibration.get(LANE_B_CANDIDATE) is None


def run(root, protocol, evidence_path, detector_sha256):
    root = Path(root).resolve()
    frozen_path = _path(root, 'freeze/protocol.json')
    if not frozen_path.is_file():
        return {'status': 'blocked', 'reasons': ['verified-shared-development-protocol-freeze-required']}
    frozen = verify_protocol(root)
    if frozen['protocol_sha256'] != stable_id(protocol):
        raise ValueError('requested protocol differs from the frozen protocol')
    if frozen['status'] != 'ready':
        return {'status': 'blocked', 'reasons': frozen['reasons']}
    lanes = {}
    if 'b' in frozen['frozen_lanes']:
        from sentinelops.ml.model_benchmark_detector_fit import FIT_PATH, verify_detector_fit, verify_protocol_detector_fit
        from sentinelops.ml.model_benchmark_protocol import _ref

        if not detector_sha256:
            return {'status': 'blocked', 'reasons': ['lane-b-fit-sha256-required']}
        fit_path = root / FIT_PATH
        if not fit_path.is_file():
            return {'status': 'blocked', 'reasons': ['verified-detector-fit-required']}
        missing_combo = {'status': 'blocked', 'reasons': ['combo-operating-point-missing']}
        try:
            detector = verify_detector_fit(root, expected_fit_sha256=detector_sha256)
        except ValueError:
            if _pinned_fit_lacks_combo(fit_path, detector_sha256):
                return missing_combo
            raise
        if detector.provenance_sha256 != protocol['data']['manifest']['sha256']:
            raise ValueError('Lane B checkpoint data differs from the frozen protocol')
        if detector.protocol_splits_sha256 != protocol['data']['splits']['sha256']:
            return {'status': 'blocked', 'reasons': ['lane-b-protocol-split-binding-required']}
        detector = verify_protocol_detector_fit(root, expected_fit_sha256=detector_sha256,
            protocol_splits=_ref(root, protocol['data']['splits']),
            protocol_splits_sha256=protocol['data']['splits']['sha256'],
            protocol_source_manifest=_ref(root, protocol['code']['source_manifest']))
        combo = detector.calibration.get(LANE_B_CANDIDATE)
        if combo is None:
            return missing_combo
        if detector.status != 'ready':
            return {'status': 'blocked', 'reasons': ['lane-b-operating-points-not-ready']}
        thresholds = combo.get('thresholds') if isinstance(combo, dict) else None
        if (not isinstance(thresholds, dict) or set(thresholds) != {'median_mad', 'adsketch'}
                or any((type(value) not in (int, float) or not math.isfinite(value) or value <= 0)
                       for model, value in thresholds.items() if model != 'adsketch' or value is not None)
                or combo.get('status') != 'ready'
                or type(combo.get('threshold')) not in (int, float) or combo['threshold'] != 1.0
                or type(combo.get('budget')) not in (int, float)
                or combo['budget'] != protocol['reporting']['lane_b']['calibration_budget']):
            return {'status': 'blocked', 'reasons': ['combo-operating-point-invalid']}
        if combo.get('selection_rule') != protocol['reporting']['lane_b']['combo_selection_rule_id']:
            return {'status': 'blocked', 'reasons': ['combo-selection-rule-mismatch']}
        lanes['b'] = {'status': detector.status, 'fit_sha256': detector.fit_sha256,
                      'model_sha256': detector.model.digest, 'persistence': detector.persistence,
                      'operating_points': detector.calibration}
    if 'a' in frozen['frozen_lanes']:
        if not evidence_path:
            return {'status': 'blocked', 'reasons': ['lane-a-evidence-required']}
        lanes['a'] = _lane_a(root, protocol, evidence_path)
        if lanes['a']['status'] != 'ready':
            return {'status': 'blocked', 'reasons': ['lane-a-candidate-not-ready']}
    baseline = json.loads(Path('configs/alibaba_local_evaluation.json').read_bytes())['candidate']
    manifest = {'schema_version': 'benchmark-candidates-freeze/development-v1',
                'protocol_sha256': frozen['protocol_sha256'], 'scope': 'development',
                'activation_allowed': False, 'holdout_opened': False, 'baseline_bundle': baseline,
                'lanes': lanes}
    if frozen['frozen_lanes'] != ['a', 'b']:
        manifest['frozen_lanes'] = frozen['frozen_lanes']
    output = _path(root, 'freeze/candidates.json')
    publish_freeze(output, _bytes(manifest), conflict='different candidates already frozen')
    return {'status': 'ready', 'scope': 'development', 'activation_allowed': False,
            'holdout_opened': False, 'candidates_sha256': file_sha256(output)}
