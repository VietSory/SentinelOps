"""Verify completed Lane A artifacts admitted by a development protocol."""
from hashlib import sha256
import json
from pathlib import Path

from sentinelops.ml.model_benchmark_protocol import _json_ref, audit_protocol
from sentinelops.public_data.alibaba import file_sha256
from sentinelops.telemetry.core import stable_id


def verify_stage(command, root, protocol, evidence_path):
    root = Path(root).resolve()
    evidence = json.loads(evidence_path.read_bytes())
    if (evidence['scope'] != 'alibaba-proxy-development'
            or evidence['activation_allowed'] is not False or evidence['holdout_opened'] is not False):
        raise ValueError('Lane A requires development-only evidence')
    audit = audit_protocol(protocol, root)
    if (protocol['data']['manifest']['sha256'] != evidence['fit_report_sha256']
            or protocol['data']['splits']['sha256'] != evidence['development_support']['split_sha256']):
        raise ValueError('Lane A evidence differs from the protocol source or split')
    support = _json_ref(root, protocol['data']['support'])['lanes']['a']
    if (stable_id(support) != evidence['development_support']['lane_a_sha256']
            or support['status'] != 'ready'
            or support['calibration_ledger_sha256'] != evidence['calibration']['ledger_sha256']):
        raise ValueError('Lane A requires verified support for this calibration ledger')
    preflight = root / 'preflight'
    fit_path = preflight / 'development-k16-fit-v5/receipt.json'
    model = preflight / 'development-k16-xgboost-v5/risk.json'
    calibration_path = preflight / 'development-k16-calibration-v5/manifest.json'
    if (file_sha256(fit_path) != evidence['fit_receipt_sha256']
            or file_sha256(model) != evidence['xgboost_model_sha256']):
        raise ValueError('Lane A fit receipt or XGBoost weights differ from evidence')
    fit = json.loads(fit_path.read_bytes())
    if (fit['input_sha256'] != evidence['fit_input_sha256']
            or {name: entry['metadata_sha256'] for name, entry in fit['models'].items()}
            != evidence['fit_metadata_sha256']):
        raise ValueError('Lane A fit identity differs from evidence')
    calibration_bytes = calibration_path.read_bytes()
    if sha256(calibration_bytes).hexdigest() != evidence['calibration']['manifest_sha256']:
        raise ValueError('Lane A calibration manifest differs from evidence')
    manifest = json.loads(calibration_bytes)
    if manifest['predictions_sha256'] != evidence['calibration']['ledger_sha256']:
        raise ValueError('Lane A calibration ledger differs from evidence')
    result = {**audit['lanes']['a'], 'protocol_sha256': audit['protocol_sha256'],
              'artifact_status': 'verified', 'scope': evidence['scope'],
              'activation_allowed': False, 'holdout_opened': False,
              'fit_receipt_sha256': evidence['fit_receipt_sha256']}
    if command == 'score':
        from scripts.verify_ltfp_development_score import verify

        paths = preflight / 'development-k16-score-v5'
        score = evidence['score']
        if (file_sha256(paths / 'manifest.json') != score['manifest_sha256']
                or file_sha256(paths / 'predictions.parquet') != score['predictions_sha256']
                or file_sha256('scripts/score_ltfp_development.py') != score['score_script_sha256']
                or file_sha256('scripts/verify_ltfp_development_score.py') != score['verify_script_sha256']):
            raise ValueError('Lane A development scores differ from evidence')
        verified = verify(root)
        result.update(windows=verified['windows'], predictions_sha256=verified['predictions_sha256'])
    else:
        from scripts.calibrate_ltfp_development import verify

        verified = verify(root)
        if command == 'fit':
            result['training_records'] = fit['unique_records_used']
        elif command == 'calibrate':
            points = manifest['points']
            thresholds = {name: point['threshold'] for name, point in points.items()}
            if thresholds != evidence['calibration']['thresholds']:
                raise ValueError('Lane A thresholds differ from evidence')
            result.update(selected_services=verified['selected_services'], thresholds=thresholds)
        else:
            raise ValueError('unsupported Lane A development stage')
    return result
