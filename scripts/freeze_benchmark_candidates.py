"""Pin both verified development candidates before preparing holdout capture."""
import json
from pathlib import Path

from scripts.verify_ltfp_development_stage import verify_stage
from sentinelops.ml.model_benchmark_protocol import _bytes, _path, publish_freeze, verify_protocol
from sentinelops.public_data.alibaba import file_sha256
from sentinelops.telemetry.core import stable_id


def _lane_a(root, protocol, evidence_path):
    stage = verify_stage('calibrate', root, protocol, evidence_path)
    preflight = root / 'preflight'
    fit_dir = preflight / 'development-k16-fit-v5'
    replay = preflight / 'independent-replay-v5'
    original = json.loads((fit_dir / 'receipt.json').read_bytes())
    settings = original['settings']
    training = protocol['training']
    if (any(settings[key] != training[key] for key in ('batch_size', 'learning_rate', 'optimizer', 'seed', 'trials'))
            or settings['steps'] != training['max_steps']
            or settings['dtype'] != protocol['features']['dtype']
            or settings['early_stopping'] is not False or training['early_stopping'] != 'none'):
        raise ValueError('Lane A fitted settings differ from the frozen protocol')
    repeated_fit = replay / fit_dir.name
    repeated = json.loads((repeated_fit / 'receipt.json').read_bytes())
    if any(original[key] != repeated[key] for key in ('input_sha256', 'settings', 'unique_records_used')):
        raise ValueError('Lane A repeated fit training identity differs')
    for name, model in original['models'].items():
        replica = repeated['models'][name]
        if (any(model[key] != replica[key] for key in ('steps', 'losses', 'metadata_sha256'))
                or any(file_sha256(fit_dir / name / filename) != file_sha256(repeated_fit / name / filename)
                       for filename in ('metadata.json', 'weights.pt'))):
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
    from sentinelops.ml.model_benchmark_detector_fit import FIT_PATH, verify_detector_fit

    if not (root / FIT_PATH).is_file():
        return {'status': 'blocked', 'reasons': ['verified-detector-fit-required']}
    detector = verify_detector_fit(root, expected_fit_sha256=detector_sha256)
    if detector.provenance_sha256 != protocol['data']['manifest']['sha256']:
        raise ValueError('Lane B checkpoint data differs from the frozen protocol')
    if detector.status != 'ready':
        return {'status': 'blocked', 'reasons': ['lane-b-operating-points-not-ready']}
    lane_a = _lane_a(root, protocol, evidence_path)
    if lane_a['status'] != 'ready':
        return {'status': 'blocked', 'reasons': ['lane-a-candidate-not-ready']}
    baseline = json.loads(Path('configs/alibaba_local_evaluation.json').read_bytes())['candidate']
    manifest = {'schema_version': 'benchmark-candidates-freeze/development-v1',
                'protocol_sha256': frozen['protocol_sha256'], 'scope': 'development',
                'activation_allowed': False, 'holdout_opened': False, 'baseline_bundle': baseline,
                'lanes': {'a': lane_a, 'b': {'status': detector.status, 'fit_sha256': detector.fit_sha256,
                    'model_sha256': detector.model.digest, 'persistence': detector.persistence,
                    'operating_points': detector.calibration}}}
    output = _path(root, 'freeze/candidates.json')
    publish_freeze(output, _bytes(manifest), conflict='different candidates already frozen')
    return {'status': 'ready', 'scope': 'development', 'activation_allowed': False,
            'holdout_opened': False, 'candidates_sha256': file_sha256(output)}
