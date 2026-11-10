"""Operator-run serving release for the ADR-016 Alibaba bundle.

Records an explicit owner G3 decision taken outside software, after checking
that every referenced piece of evidence matches the exact bundle: the frozen fit
report (G2 basis: checksummed archives, identity splits, frozen config), the
layer-1 controls report and a passing profile-v2 source parity. It writes a new
trusted store and activates nothing; B selects the release in serving settings.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path

from sentinelops.ml.alibaba_bundle import BUNDLE_VERSION, ORIGIN
from sentinelops.ml.release import APPROVAL_VERSION, PARITY_VERIFICATION_VERSION, PARITY_VERSION, RELEASE_VERSION, \
    parity_verification_ref
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.contracts import utc_time
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2, queries
from sentinelops.telemetry.store import ArtifactStore

PARITY_CRITERIA = 'alibaba-parity-criteria/v1'
CRITERIA = ('P1', 'P2', 'P3', 'P4', 'P5', 'P6')
BUNDLE_DIRECTORY = 'alibaba-v2022'
HEALTH_POLICY = 'configs/slo_health_policy.json'  # the frozen policy the parity script applies


def _pinned(path, sha256):
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError(f'{Path(path).name} differs from its pin')
    return data


DETECTOR_METRICS = ('f1', 'precision', 'recall', 'false_incidents_per_healthy_service_hour')
RISK_METRICS = ('average_precision', 'prevalence', 'precursor_recall_lead_ge_60s', 'actual_alert_rate_on_negatives',
                'threshold')
CONTROL_SECTIONS = (('detector_events', ('isolation_forest', 'median_mad'), DETECTOR_METRICS),
                    ('risk', ('xgboost', 'slope'), RISK_METRICS))
UNIT_INTERVAL = {'f1', 'precision', 'recall', 'average_precision', 'prevalence', 'precursor_recall_lead_ge_60s',
                 'actual_alert_rate_on_negatives'}
BOOTSTRAP_INTERVALS = ('if_minus_mad_event_f1', 'xgb_minus_slope_average_precision')


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _close(recorded, derived):
    return _number(recorded) and math.isclose(recorded, derived, rel_tol=1e-9, abs_tol=1e-12)


def controls_complete(controls):
    """The layer-1 comparison the owner reads before G3 must be present, not only its identity."""
    for section, models, metrics in CONTROL_SECTIONS:
        block = controls.get(section)
        if not isinstance(block, dict):
            return False
        for model in models:
            values = block.get(model)
            if not isinstance(values, dict) or not all(_number(values.get(m)) for m in metrics):
                return False
            if not all(0 <= values[m] <= 1 for m in metrics if m in UNIT_INTERVAL) or any(
                    values[m] < 0 for m in metrics):
                return False
    # Codex review: a probability threshold outside [0, 1] or a negative slope threshold is invalid.
    if not 0 <= controls['risk']['xgboost']['threshold'] <= 1 or controls['risk']['slope']['threshold'] < 0:
        return False
    # Codex review: rates must follow from the counts recorded beside them, not be free numbers.
    for model in ('isolation_forest', 'median_mad'):
        d = controls['detector_events'][model]
        if not all(_count(d.get(k)) for k in ('tp', 'fp', 'fn')):
            return False
        tp, fp, fn = d['tp'], d['fp'], d['fn']
        if not (_close(d['precision'], tp / (tp + fp) if tp + fp else 0.0)
                and _close(d['recall'], tp / (tp + fn) if tp + fn else 0.0)
                and _close(d['f1'], 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0)):
            return False
    for model in ('xgboost', 'slope'):
        r = controls['risk'][model]
        if not all(_count(r.get(k)) for k in ('positives', 'windows', 'eligible_precursor_events',
                                               'detected_precursor_events')):
            return False
        if (r['windows'] < 1 or r['positives'] > r['windows'] or not _close(r['prevalence'], r['positives'] / r['windows'])
                or r['eligible_precursor_events'] < 1 or r['detected_precursor_events'] > r['eligible_precursor_events']
                or r['precursor_recall_lead_ge_60s'] > r['detected_precursor_events'] / r['eligible_precursor_events']
                + 1e-12):
            return False
    bootstrap = controls.get('paired_service_bootstrap')
    if not isinstance(bootstrap, dict) or not _number(bootstrap.get('services')) or bootstrap['services'] < 2:
        return False
    for name in BOOTSTRAP_INTERVALS:
        interval = bootstrap.get(name)
        if (not isinstance(interval, dict) or not _number(interval.get('valid_replicates'))
                or interval['valid_replicates'] < 1
                or not all(_number(interval.get(k)) for k in ('lower', 'upper'))
                or not -1 <= interval['lower'] <= interval['upper'] <= 1):
            return False  # differences of two [0, 1] metrics lie in [-1, 1]
    return True


BUCKET_CRITERIA = ('P3', 'P4', 'P5')
# Same bounds as scripts/check_alibaba_source_parity.py (a test keeps them equal).
COUNT_TOLERANCE = .05
MEAN_TOLERANCE = .10


def criteria_complete(criteria, model_version, config, scores_for, health_policy):
    """Every P1–P6 criterion needs at least one recorded check, and every check must pass.

    P1 must name this bundle's model version. P2 declares the (service, bucket) pairs of the
    run, one per raw capture, and must cover every business route with the same buckets. P3,
    P4 and P5 must each check exactly those pairs, and the P3 summary must count them, so a
    verification that keeps only a summary line or a subset of buckets cannot certify the
    bundle (Codex review). P6 needs one final window per route.
    """
    if not (isinstance(criteria, dict) and set(criteria) == set(CRITERIA)
            and all(isinstance(criteria[name], list) and criteria[name]
                    and all(isinstance(item, dict) and item.get('ok') is True for item in criteria[name])
                    for name in CRITERIA)):
        return False
    if any(item.get('model_version') != model_version for item in criteria['P1']):
        return False
    def pairs(items):
        return [(item.get('service'), item.get('bucket_end')) for item in items if 'bucket_end' in item]
    declared = pairs(criteria['P2'])
    if len(declared) != len(criteria['P2']) or len(set(declared)) != len(declared):
        return False
    buckets = {service: {end for s, end in declared if s == service} for service in BUSINESS_ROUTES}
    if ({s for s, _ in declared} != set(BUSINESS_ROUTES) or len({frozenset(v) for v in buckets.values()}) != 1
            or not all(isinstance(end, str) and end for _, end in declared)):
        return False
    for name in BUCKET_CRITERIA:
        checked = pairs(criteria[name])
        if sorted(checked) != sorted(declared):
            return False
    summaries = [item for item in criteria['P3'] if 'bucket_end' not in item]
    if len(summaries) != 1 or summaries[0].get('observations') != len(declared):
        return False
    # The run must span the bundle's own minimum history as consecutive minutes (window plus
    # minimum baseline plus one spare, as check_alibaba_source_parity derives it), and P6 must
    # score the final minute of that run for every route (Codex review: a uniformly truncated
    # parity of one minute per service, or a gap, must not certify the bundle).
    try:
        minutes = sorted(utc_time(end) for end in next(iter(buckets.values())))
    except (TypeError, ValueError):
        return False
    required = config['window_minutes'] + config['baseline']['min_minutes'] + 1
    if (len(minutes) < required or any(minute.timestamp() % 60 for minute in minutes)
            or any((b - a).total_seconds() != 60 for a, b in zip(minutes, minutes[1:]))):
        return False
    final = timestamp(minutes[-1])
    if not (sorted(item.get('service') for item in criteria['P6']) == sorted(BUSINESS_ROUTES)
            and all(item.get('window_end') == final for item in criteria['P6'])):
        return False
    # Do not trust `ok` alone (Codex reviews): each recorded measurement must satisfy the parity
    # script's bound (same constants, kept equal by a test), the derived difference must follow
    # from the raw values recorded beside it, and P6 scores are recomputed with the bundle itself.
    # Every condition the parity script checks must be recorded and re-checked here (Codex review
    # of a55bb05): P2 the capture's profile and pinned queries, P3 eligibility and the frozen SLO
    # health policy's maximum lag, P4 no 5xx.
    for item in criteria['P2']:
        if (item.get('source_profile') != SOURCE_PROFILE_V2
                or item.get('queries_sha256') != stable_id(queries(item['service'], SOURCE_PROFILE_V2))):
            return False
    max_lag = health_policy['maximum_lag_seconds']
    for item in criteria['P3']:
        if 'bucket_end' in item and not (_number(item.get('coverage')) and item['coverage'] == 1
                                         and item.get('eligible_for_inference') is True
                                         and _number(item.get('lag_seconds')) and 0 <= item['lag_seconds'] <= max_lag):
            return False
    for item in criteria['P4']:
        count, completions = item.get('prometheus_count'), item.get('server_completions')
        if not (_number(count) and count >= 0 and _count(completions) and completions > 0
                and item.get('error_count') == 0 and _count(item.get('error_count'))
                and _close(item.get('relative_difference'), abs(count / completions - 1))
                and item['relative_difference'] <= COUNT_TOLERANCE):
            return False
    for item in criteria['P5']:
        mean, exact = item.get('prometheus_mean_ms'), item.get('exact_mean_ms')
        if not (_number(mean) and mean > 0 and _number(exact) and exact > 0
                and _close(item.get('relative_difference'), abs(mean / exact - 1))
                and item['relative_difference'] <= MEAN_TOLERANCE):
            return False
    for item in criteria['P6']:
        features, scores = item.get('features'), item.get('scores')
        if (not isinstance(features, dict) or set(features) != set(config['features'])  # the store sorts JSON keys
                or not all(_number(v) for v in features.values())
                or not isinstance(scores, dict) or set(scores) != {'isolation_forest', 'median_mad', 'xgboost'}):
            return False
        expected = scores_for([features[name] for name in config['features']])
        if set(expected) != set(scores) or not all(_close(scores[k], expected[k]) for k in expected):
            return False
    return True


def bundle_scorer(bundle, manifest_sha256):
    """Score one feature vector with the bundle's own models, exactly as the parity script's P6."""
    from sentinelops.ml.models import load_bundle
    _, detector, risk = load_bundle(Path(bundle), expected_manifest_sha256=manifest_sha256)

    def scores_for(values):
        detection = detector.predict([values])
        scores = {name: float(item['raw_score'][0]) for name, item in detection.items()}
        scores['xgboost'] = float(risk.predict_proba([values])[0, 1])
        return scores
    return scores_for


def validate_evidence(manifest, report, controls, parity_record, parity_verification, manifest_sha256, *,
                      scores_for, health_policy=None):
    """Completeness and internal consistency of operator-supplied evidence.

    Trust boundary: these documents come from operator-run scripts (alibaba_controls,
    check_alibaba_source_parity) and are not signed. This check proves that they are complete,
    bound to this bundle, and consistent with the raw values recorded inside them, and it
    recomputes P6 with the bundle. It cannot prove that the recorded raw values were measured
    rather than written by hand. That rests on the operator who runs parity on the target
    environment and on the owner's G3 decision recorded with the release.
    """
    provenance = manifest['provenance']
    model_version = manifest['model_version']
    if (manifest.get('bundle_version') != BUNDLE_VERSION or manifest.get('origin') != ORIGIN
            or report.get('report_version') != 'alibaba-relative-development/v1'
            or report.get('config_sha256') != provenance['config_sha256']
            or len(report.get('source_archives', [])) != len(provenance['source_archives'])
            or any(report['counts'][k]['services'] <= 0 for k in ('train', 'calibration', 'evaluation'))):
        raise ValueError('fit report does not support G2 for this bundle')
    if (controls.get('report_version') != 'alibaba-relative-controls/v1'
            or controls.get('fit_report_sha256') != stable_id(report)
            or controls.get('config_sha256') != provenance['config_sha256']
            or controls.get('no_refit_or_retune') is not True):
        raise ValueError('layer-1 controls do not belong to this fit')
    if not controls_complete(controls):
        raise ValueError('layer-1 controls lack the detector, risk or bootstrap metrics')
    if controls.get('model_artifacts_sha256') != manifest['artifacts']:
        raise ValueError('layer-1 controls did not score this bundle\'s model files')
    if health_policy is None:
        health_policy = json.loads((Path(__file__).resolve().parents[3] / HEALTH_POLICY).read_text())
    scope = {'candidate_id': model_version, 'source_profile': SOURCE_PROFILE_V2}
    if (not isinstance(parity_record, dict) or parity_record.get('parity_version') != PARITY_VERSION
            or parity_record.get('status') != 'PASS'
            or any(parity_record.get(k) != v for k, v in scope.items())
            or parity_record.get('evidence') != [parity_verification_ref(parity_verification)]
            or {m['service'] for m in parity_record.get('models', [])} != set(BUSINESS_ROUTES)):
        raise ValueError('source parity record does not certify this bundle on profile v2')
    criteria = parity_verification.get('criteria', {})
    if (parity_verification.get('version') != PARITY_VERIFICATION_VERSION
            or parity_verification.get('criteria_version') != PARITY_CRITERIA
            or parity_verification.get('failures') != []
            or parity_verification.get('bundle_manifest_sha256') != manifest_sha256
            or any(parity_verification.get(k) != parity_record.get(k) for k in ('candidate_id', 'environment',
                                                                                 'source_profile', 'verified_at'))
            or not criteria_complete(criteria, model_version, provenance['config'], scores_for, health_policy)):
        raise ValueError('source parity verification lacks complete passing P1–P6 checks')


def build_alibaba_release(bundle, manifest_sha256, *, controls, parity_record, parity_verification,
                          approved_by, decision_note, output, now=None):
    from sentinelops.ml.models import read_bundle_manifest
    from sentinelops.ml.registry import ModelRegistry
    bundle, output = Path(bundle).resolve(strict=True), Path(output)
    if output.exists():
        raise FileExistsError('release output must be a new directory')
    for name, value in (('approved_by', approved_by), ('decision_note', decision_note)):
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError(name + ' must be a nonempty explicit owner decision')
    manifest = read_bundle_manifest(bundle, expected_manifest_sha256=manifest_sha256)
    report = json.loads(_pinned(bundle / 'report.json', manifest['provenance']['report_sha256']))
    validate_evidence(manifest, report, controls, parity_record, parity_verification, manifest_sha256,
                      scores_for=bundle_scorer(bundle, manifest_sha256))
    decided = timestamp(now or datetime.now(timezone.utc))
    staging = output.with_name('.' + output.name + '.staging')
    if staging.exists():
        raise FileExistsError('a previous release build left a staging directory; investigate it')
    staging.mkdir(parents=True)
    target = staging / BUNDLE_DIRECTORY
    target.mkdir()
    for name, sha in (('manifest.json', manifest_sha256), ('report.json', manifest['provenance']['report_sha256']),
                      *manifest['artifacts'].items()):
        (target / name).write_bytes(_pinned(bundle / name, sha))
    store = ArtifactStore(staging)
    evidence = [store.write('manifests', value) for value in (report, controls, parity_record, parity_verification)]
    provenance = manifest['provenance']
    approval = {'approval_version': APPROVAL_VERSION, 'candidate_id': manifest['model_version'],
                'service': 'all-business-routes', 'model_version': manifest['model_version'],
                'G2': 'PASS', 'G3': 'PASS', 'source_parity': 'PASS', 'activation_allowed': True,
                'evaluation_refs': evidence, 'source_profile': SOURCE_PROFILE_V2,
                'report_sha256': provenance['report_sha256'], 'config_sha256': provenance['config_sha256'],
                'g2_basis': 'checksummed Alibaba archives, identity splits and config frozen before fit',
                'approved_by': approved_by.strip(), 'decision_note': decision_note.strip(), 'approved_at': decided}
    rows = [{'bundle_directory': BUNDLE_DIRECTORY, 'manifest_sha256': manifest_sha256,
             'validation_ref': store.write('manifests', approval)}]
    release = {'release_version': RELEASE_VERSION, 'candidate_id': manifest['model_version'], 'models': rows,
               'built_at': decided, 'approved_by': approved_by.strip()}
    reference = store.write('manifests', release)
    loaded = ModelRegistry.load(ArtifactStore(staging), reference)
    if not loaded.available or loaded.identity != reference or getattr(loaded, 'is_alibaba', False) is not True:
        raise ValueError('built release does not load as a validated Alibaba registry')
    os.rename(staging, output)
    return {'release_ref': reference, 'store': str(output), 'model_version': manifest['model_version'],
            'activation_performed': False}
