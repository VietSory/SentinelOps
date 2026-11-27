"""Baseline inference on the exact accepted relative frame used by benchmark labels."""
import hashlib
import json
import math
import re
from pathlib import Path

from sentinelops.contracts import utc_time
from sentinelops.ml.alibaba_live import project_relative_window
from sentinelops.ml.models import load_bundle
from sentinelops.telemetry.core import stable_id


def baseline_scores(store, batch_ref, service, *, as_of, bundle, manifest_sha256, loaded=None):
    """Load a trusted pinned bundle; return scores and its immutable causal frame.

    The caller supplies a trusted local bundle, never a user-uploaded pickle.
    No release is activated; model identity is returned for explicit comparison.
    """
    if loaded is None:
        manifest, _, risk = load_bundle(Path(bundle), expected_manifest_sha256=manifest_sha256)
    else:
        if (Path(loaded['directory']).resolve() != Path(bundle).resolve()
                or loaded['manifest_sha256'] != manifest_sha256):
            raise ValueError('preloaded baseline differs from the pinned bundle')
        manifest, risk = loaded['manifest'], loaded['risk']
    config = manifest['provenance']['config']
    if (stable_id(config) != manifest['provenance']['config_sha256']
            or config['features'] != manifest['ordered_features']):
        raise ValueError('baseline config or feature order differs from pinned model')
    if risk is None:
        raise ValueError('baseline requires a risk estimator')
    cutoff = utc_time(as_of) if isinstance(as_of, str) else as_of
    end = utc_time(store.read(batch_ref)['bucket_end'])
    if cutoff < end:
        raise ValueError('baseline cutoff precedes anchor')
    provenance = {'model_version': manifest['model_version'], 'manifest_sha256': manifest_sha256,
                  'config_sha256': stable_id(config), 'batch_ref': batch_ref}
    reason = None
    projection = None
    if (cutoff - end).total_seconds() >= 60:
        reason = 'forecast-anchor-minute-advanced'
    else:
        projection = project_relative_window(store, batch_ref, service, as_of=cutoff, config=config)
        if projection is None:
            reason = 'incomplete-feature-history'
    if projection is None:
        return {**provenance, 'feature_ref': None, 'scores': {
            name: {'score': None, 'missing_reason': reason} for name in ('slope', 'xgboost')}}
    if (projection['feature_set_version'] != config['feature_set_version']
            or list(projection['features']) != config['features']):
        raise ValueError('projection feature identity differs from pinned model')
    values = [projection['features'][name] for name in config['features']]
    if not all(math.isfinite(value) for value in values):
        raise ValueError('baseline features must be finite')
    probability = float(risk.predict_proba([values])[0][1])
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError('baseline risk probability is invalid')
    frame = {**projection, **provenance}
    return {**provenance, 'feature_ref': store.write('features', frame), 'scores': {
        'slope': {'score': max(0., projection['features']['rt_slope_rel_per_min']), 'missing_reason': None},
        'xgboost': {'score': probability, 'missing_reason': None}}}


def refit_score(store, baseline, config, *, directory, manifest_sha256, fit_input_sha256, loaded=None):
    """Score the accepted frame with the pinned same-split development refit."""
    from xgboost import XGBClassifier
    from sentinelops.ml.models import ExceedanceRisk

    if any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
           for value in (manifest_sha256, fit_input_sha256)):
        raise ValueError('refit manifest and paired training pins required')
    directory = Path(directory)
    if loaded is None:
        content = (directory / 'manifest.json').read_bytes()
        if hashlib.sha256(content).hexdigest() != manifest_sha256:
            raise ValueError('refit manifest SHA256 mismatch')
        manifest = json.loads(content)
        model_path = directory / 'risk.json'
    else:
        if (Path(loaded['directory']).resolve() != directory.resolve()
                or loaded['manifest_sha256'] != manifest_sha256 or loaded['fit_input_sha256'] != fit_input_sha256):
            raise ValueError('preloaded refit differs from its pinned identity')
        manifest = loaded['manifest']
        model_path = None
    if (manifest.get('scope') != 'development-train-only' or manifest.get('activation_allowed') is not False
            or manifest.get('paired_fit_input_sha256') != fit_input_sha256
            or manifest.get('config_sha256') != stable_id(config)
            or (model_path is not None and hashlib.sha256(model_path.read_bytes()).hexdigest() != manifest.get('model_sha256'))):
        raise ValueError('refit model or paired training identity differs')
    if loaded is None:
        model = XGBClassifier()
        model.load_model(model_path)
    else:
        model = loaded['model']
    provenance = {'scope': 'development-refit', 'manifest_sha256': manifest_sha256,
                  'model_sha256': manifest['model_sha256'], 'fit_input_sha256': fit_input_sha256}
    if baseline['feature_ref'] is None:
        return baseline['scores']['xgboost'], provenance
    frame = store.read(baseline['feature_ref'])
    values = [frame['features'][name] for name in config['features']]
    probability = float(ExceedanceRisk(model, config['features']).predict_proba([values])[0][1])
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError('refit risk probability is invalid')
    return {'score': probability, 'missing_reason': None}, provenance
