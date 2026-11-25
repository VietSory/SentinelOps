"""Baseline inference on the exact accepted relative frame used by benchmark labels."""
import math
from pathlib import Path

from sentinelops.contracts import utc_time
from sentinelops.ml.alibaba_live import project_relative_window
from sentinelops.ml.models import load_bundle
from sentinelops.telemetry.core import stable_id


def baseline_scores(store, batch_ref, service, *, as_of, bundle, manifest_sha256):
    """Load a trusted pinned bundle; return scores and its immutable causal frame.

    The caller supplies a trusted local bundle, never a user-uploaded pickle.
    No release is activated; model identity is returned for explicit comparison.
    """
    manifest, _, risk = load_bundle(Path(bundle), expected_manifest_sha256=manifest_sha256)
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
