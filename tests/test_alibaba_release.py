"""ADR-016 release builder: evidence must bind the exact bundle; nothing is activated."""
import copy
import json

import pytest

pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("xgboost")

from sentinelops.ml.alibaba_bundle import package_fit
from sentinelops.ml.alibaba_release import CRITERIA, build_alibaba_release, bundle_scorer, derived_features, validate_evidence
from sentinelops.ml.models import read_bundle_manifest
from sentinelops.ml.registry import ModelRegistry
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2, queries
from sentinelops.ml.release import parity_verification_ref
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id
from tests.test_alibaba_bundle import synthetic_fit


BUCKETS = tuple(f'2026-11-09T02:{minute:02d}:00Z' for minute in range(20, 31))  # window 5 + baseline 5 + 1


def criteria(model_version, config, scorer, buckets=BUCKETS):
    pairs = [{'service': s, 'bucket_end': b} for s in sorted(BUSINESS_ROUTES) for b in buckets]
    means = {b: 15.0 + i * 0.2 for i, b in enumerate(buckets)}  # a gentle trend, so features are non-trivial
    built = {'P1': [{'ok': True, 'model_version': model_version}],
             'P2': [{'ok': True, 'source_profile': SOURCE_PROFILE_V2, 'pod_identity_verified': True,
                     'queries_sha256': stable_id(queries(p['service'], SOURCE_PROFILE_V2)), **p} for p in pairs],
             'P3': [{'ok': True, 'what': 'all buckets normalized', 'observations': len(pairs)}]
                   + [{'ok': True, 'coverage': 1.0, 'lag_seconds': 35.0, 'eligible_for_inference': True,
                      'maximum_lag_seconds': 60, **p} for p in pairs],
             'P4': [{'ok': True, 'prometheus_count': 58.0, 'server_completions': 60,
                     'relative_difference': abs(58.0 / 60 - 1), 'error_count': 0, 'failed_requests': 0, **p}
                   for p in pairs],
             'P5': [{'ok': True, 'prometheus_mean_ms': means[p['bucket_end']], 'exact_mean_ms': means[p['bucket_end']] * 0.99,
                     'relative_difference': abs(1 / 0.99 - 1), **p} for p in pairs]}
    built['P6'] = []
    for service in sorted(BUSINESS_ROUTES):
        features = derived_features(built, service, config)
        built['P6'].append({'ok': True, 'service': service, 'window_end': buckets[-1], 'features': features,
                            'scores': scorer([features[name] for name in config['features']])})
    return built


def scorer(tmp_path, sha):
    return bundle_scorer(tmp_path / 'bundle', sha)


def truncate(v, keep):
    # Codex review: cut every service's P2–P5 uniformly and fix the P3 count accordingly.
    kept = set(keep)
    for name in ('P2', 'P3', 'P4', 'P5'):
        v['criteria'][name] = [i for i in v['criteria'][name] if 'bucket_end' not in i or i['bucket_end'] in kept]
    summary = next(i for i in v['criteria']['P3'] if 'bucket_end' not in i)
    summary['observations'] = len(kept) * len(BUSINESS_ROUTES)


def evidence(tmp_path):
    fit = synthetic_fit(tmp_path)
    report = json.loads((fit / 'report.json').read_text())
    report['counts'] = {k: {'services': 3} for k in ('train', 'calibration', 'evaluation')}
    (fit / 'report.json').write_text(json.dumps(report, default=float))
    result = package_fit(fit, tmp_path / 'bundle')
    manifest = read_bundle_manifest(tmp_path / 'bundle', expected_manifest_sha256=result['manifest_sha256'])
    report = json.loads((tmp_path / 'bundle' / 'report.json').read_text())
    detector = {'tp': 10, 'fp': 30, 'fn': 20, 'precision': 10 / 40, 'recall': 10 / 30, 'f1': 20 / 70,
                'false_incidents_per_healthy_service_hour': 0.1}
    risk = {'average_precision': 0.07, 'positives': 10, 'windows': 1000, 'prevalence': 0.01,
            'eligible_precursor_events': 20, 'detected_precursor_events': 5, 'precursor_recall_lead_ge_60s': 0.2,
            'actual_alert_rate_on_negatives': 0.02, 'threshold': 0.8}
    interval = {'valid_replicates': 1000, 'lower': -0.02, 'upper': 0.04}
    controls = {'report_version': 'alibaba-relative-controls/v1', 'fit_report_sha256': stable_id(report),
                'config_sha256': report['config_sha256'], 'no_refit_or_retune': True,
                'detector_events': {'isolation_forest': dict(detector), 'median_mad': dict(detector)},
                'risk': {'xgboost': dict(risk), 'slope': dict(risk)},
                'paired_service_bootstrap': {'services': 3, 'if_minus_mad_event_f1': dict(interval),
                                             'xgb_minus_slope_average_precision': dict(interval)},
                'model_artifacts_sha256': dict(manifest['artifacts'])}
    scope = {'candidate_id': manifest['model_version'], 'environment': 'test', 'source_profile': 'prometheus-increase-60s-5s/v2',
             'models': [{'service': s, 'model_version': manifest['model_version']} for s in sorted(BUSINESS_ROUTES)],
             'verified_by': 'test', 'verified_at': '2026-11-09T00:00:00Z'}
    verification = {'version': 'model-source-parity-check/v1', 'criteria_version': 'alibaba-parity-criteria/v1', **scope,
                    'bundle_manifest_sha256': result['manifest_sha256'], 'failures': [],
                    'config_sha256': manifest['provenance']['config_sha256'], 'source_commit': 'a' * 40,
                    'scrape_interval': '5s', 'configuration_hash': 'b' * 64,
                    'criteria': criteria(manifest['model_version'], manifest['provenance']['config'],
                                         bundle_scorer(tmp_path / 'bundle', result['manifest_sha256']))}
    record = {'parity_version': 'model-source-parity/v1', **scope, 'status': 'PASS',
              'evidence': [parity_verification_ref(verification)]}
    return result['manifest_sha256'], manifest, report, controls, record, verification


def test_evidence_accepts_exact_bundle_and_rejects_drift(tmp_path):
    sha, manifest, report, controls, record, verification = evidence(tmp_path)
    validate_evidence(manifest, report, controls, record, verification, sha, scores_for=scorer(tmp_path, sha),
                      repository_config=manifest['provenance']['config'])
    for mutate in (lambda c, r, v: c.update(fit_report_sha256='0' * 64),
                   lambda c, r, v: r.update(status='FAIL'),
                   lambda c, r, v: v['criteria']['P5'].append({'ok': False}),
                   lambda c, r, v: v.pop('criteria') and v.update(criteria={'P1': [{'ok': True}]}),
                   lambda c, r, v: v.update(bundle_manifest_sha256='1' * 64),
                   lambda c, r, v: r.update(source_profile='prometheus-increase-60s-5s/v1')):
        c, r, v = copy.deepcopy(controls), copy.deepcopy(record), copy.deepcopy(verification)
        mutate(c, r, v)
        if r is not record and r.get('evidence') == record['evidence'] and v != verification:
            r['evidence'] = [parity_verification_ref(v)]
        with pytest.raises(ValueError):
            validate_evidence(manifest, report, c, r, v, sha, scores_for=scorer(tmp_path, sha),
                      repository_config=manifest['provenance']['config'])


@pytest.mark.parametrize('mutate', [
    lambda c, v: v['criteria'].update(P3=[]),
    lambda c, v: v.update(criteria={name: [] for name in CRITERIA}),
    lambda c, v: v['criteria']['P6'].append({}),
    lambda c, v: v['criteria']['P2'].append({'ok': 'true'}),
    lambda c, v: c.pop('risk'),
    lambda c, v: c['risk'].pop('slope'),
    lambda c, v: c['risk']['xgboost'].update(precursor_recall_lead_ge_60s=None),
    lambda c, v: c['detector_events']['median_mad'].pop('f1'),
    lambda c, v: c['detector_events']['isolation_forest'].update(recall=float('nan')),
    lambda c, v: c['paired_service_bootstrap'].pop('xgb_minus_slope_average_precision'),
    lambda c, v: c['paired_service_bootstrap']['if_minus_mad_event_f1'].update(valid_replicates=0),
    lambda c, v: v['criteria'].update(P4=[i for i in v['criteria']['P4'] if i['service'] == 'checkout-service']),
    lambda c, v: v['criteria'].update(P3=[i for i in v['criteria']['P3'] if 'bucket_end' not in i]),
    lambda c, v: v['criteria'].update(P3=v['criteria']['P3'][:-1]),
    lambda c, v: v['criteria']['P5'].append({'ok': True, 'service': 'payment-service', 'bucket_end': 'x'}),
    lambda c, v: v['criteria'].update(P2=[i for i in v['criteria']['P2'] if i != v['criteria']['P2'][-1]]),
    lambda c, v: v['criteria']['P3'][0].update(observations=1),
    lambda c, v: c['risk']['xgboost'].update(threshold=4.0),
    lambda c, v: c['paired_service_bootstrap']['if_minus_mad_event_f1'].update(lower=-2, upper=2),
    lambda c, v: c['model_artifacts_sha256'].update({'risk.json': '0' * 64}),
    lambda c, v: c.pop('model_artifacts_sha256'),
    lambda c, v: truncate(v, BUCKETS[-1:]),
    lambda c, v: truncate(v, BUCKETS[:5] + BUCKETS[6:]) or [i.update(window_end=BUCKETS[-1]) for i in v['criteria']['P6']],
    lambda c, v: [i.update(window_end=BUCKETS[-2]) for i in v['criteria']['P6']],
    lambda c, v: v['criteria']['P4'][3].update(relative_difference=0.5),
    lambda c, v: v['criteria']['P5'][7].pop('relative_difference'),
    lambda c, v: v['criteria']['P3'][2].update(coverage=0.9),
    lambda c, v: v['criteria']['P6'][1]['scores'].update(xgboost=1.5),
    lambda c, v: v['criteria']['P6'][0].update(features={}),
    lambda c, v: v['criteria'].update(P6=[i for i in v['criteria']['P6'] if i['service'] != 'payment-service']),
    lambda c, v: v['criteria']['P1'][0].update(model_version='0' * 64),
    lambda c, v: c['risk']['xgboost'].update(precursor_recall_lead_ge_60s=1.5),
    lambda c, v: c['detector_events']['median_mad'].update(false_incidents_per_healthy_service_hour=-0.1),
    lambda c, v: v['criteria']['P4'][0].update(prometheus_count=0, relative_difference=0.0),
    lambda c, v: c['detector_events']['isolation_forest'].update(f1=0.99),
    lambda c, v: [i.update(coverage=True) for i in v['criteria']['P3'] if 'bucket_end' in i],
    lambda c, v: [i.update(bucket_end=i['bucket_end'].replace(':00Z', ':30Z')) for name in ('P2', 'P3', 'P4', 'P5')
                  for i in v['criteria'][name] if 'bucket_end' in i]
                 and [i.update(window_end=BUCKETS[-1].replace(':00Z', ':30Z')) for i in v['criteria']['P6']],
    lambda c, v: v['criteria']['P5'][4].update(exact_mean_ms=30.0),
    lambda c, v: v['criteria']['P6'][2]['scores'].update(isolation_forest=v['criteria']['P6'][2]['scores']['isolation_forest'] + 0.01),
    lambda c, v: c['risk']['slope'].update(prevalence=0.5),
    lambda c, v: c['risk']['xgboost'].update(precursor_recall_lead_ge_60s=0.9),
    lambda c, v: v['criteria']['P3'][5].pop('lag_seconds'),
    lambda c, v: [i.update(lag_seconds=61.0) for i in v['criteria']['P3'] if 'bucket_end' in i],
    lambda c, v: v['criteria']['P3'][3].update(eligible_for_inference=False),
    lambda c, v: v['criteria']['P4'][2].update(error_count=3),
    lambda c, v: v['criteria']['P4'][2].pop('error_count'),
    lambda c, v: v['criteria']['P2'][1].update(source_profile='prometheus-increase-60s-5s/v1'),
    lambda c, v: v['criteria']['P2'][1].update(queries_sha256='0' * 64),
    lambda c, v: v.update(config_sha256='0' * 64),
    lambda c, v: v.pop('source_commit'),
    lambda c, v: v.pop('configuration_hash'),
    lambda c, v: v.update(scrape_interval='15s'),
    lambda c, v: v['criteria']['P2'][4].update(pod_identity_verified=False),
    lambda c, v: v['criteria']['P4'][6].update(failed_requests=2),
], ids=['empty-P3', 'all-criteria-empty', 'check-without-ok', 'ok-not-true', 'no-risk', 'no-slope-control',
        'precursor-recall-missing', 'detector-f1-missing', 'detector-nan', 'no-ap-interval', 'no-valid-replicates', 'P4-one-service', 'P3-summary-only', 'P3-missing-bucket', 'P5-extra-bucket',
        'P2-uneven-buckets', 'P3-summary-miscount', 'xgb-threshold-4', 'bootstrap-out-of-range', 'controls-other-risk-model',
        'controls-without-model-hashes', 'parity-one-minute-each', 'parity-gap', 'P6-not-final-minute',
        'P4-ok-but-over-tolerance', 'P5-no-measurement', 'P3-partial-coverage', 'P6-probability-above-one', 'P6-no-features',
        'P6-missing-payment', 'P1-other-model', 'recall-above-one', 'negative-false-incident-rate',
        'P4-zero-count-claims-zero-diff', 'controls-f1-not-from-counts', 'P3-boolean-coverage', 'unaligned-minutes',
        'P5-diff-not-from-means', 'P6-score-not-from-bundle', 'prevalence-not-from-counts',
        'precursor-recall-above-detected', 'P3-no-lag', 'P3-lag-over-policy', 'P3-not-eligible', 'P4-5xx',
        'P4-no-error-count', 'P2-other-profile', 'P2-other-queries', 'verified-other-config', 'no-source-commit', 'no-configuration-hash', 'scrape-15s', 'P2-pod-not-verified', 'P4-failed-requests'])
def test_evidence_rejects_empty_parity_checks_and_incomplete_controls(tmp_path, mutate):
    sha, manifest, report, controls, record, verification = evidence(tmp_path)
    c, v = copy.deepcopy(controls), copy.deepcopy(verification)
    mutate(c, v)
    r = dict(record, evidence=[parity_verification_ref(v)])
    with pytest.raises(ValueError):
        validate_evidence(manifest, report, c, r, v, sha, scores_for=scorer(tmp_path, sha),
                      repository_config=manifest['provenance']['config'])


def test_release_requires_an_explicit_owner_decision(tmp_path):
    sha, _, _, controls, record, verification = evidence(tmp_path)
    with pytest.raises(ValueError):
        build_alibaba_release(tmp_path / 'bundle', sha, controls=controls, parity_record=record,
                              parity_verification=verification, approved_by=' ', decision_note='x',
                              output=tmp_path / 'release')
    assert not (tmp_path / 'release').exists()


@pytest.mark.skipif(not hasattr(ModelRegistry(), 'is_alibaba'), reason='registry v3 consumer not merged yet')
def test_release_loads_as_one_shared_alibaba_registry(tmp_path, monkeypatch):
    sha, manifest, _, controls, record, verification = evidence(tmp_path)
    import sentinelops.ml.alibaba_training as training
    monkeypatch.setattr(training, 'load_config', lambda: manifest['provenance']['config'])
    result = build_alibaba_release(tmp_path / 'bundle', sha, controls=controls, parity_record=record,
                                   parity_verification=verification, approved_by='owner', decision_note='G3 accepted',
                                   output=tmp_path / 'release')
    assert result['activation_performed'] is False and result['model_version'] == manifest['model_version']


def test_validator_bounds_equal_the_parity_script():
    import importlib
    script = importlib.import_module('scripts.check_alibaba_source_parity')
    from sentinelops.ml import alibaba_release
    assert (alibaba_release.COUNT_TOLERANCE, alibaba_release.MEAN_TOLERANCE) == (script.COUNT_TOLERANCE,
                                                                                  script.MEAN_TOLERANCE)


def test_parity_script_records_every_field_the_validator_rechecks():
    """Producer/consumer contract (Codex review of a55bb05): the validator can only re-check what
    check_alibaba_source_parity writes into each criterion's detail."""
    import ast
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'scripts/check_alibaba_source_parity.py').read_text()
    recorded = {}
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'check' and len(node.args) == 3
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[2], ast.Dict)):
            recorded.setdefault(node.args[0].value, set()).update(
                k.value for k in node.args[2].keys if isinstance(k, ast.Constant))
    required = {'P1': {'model_version'}, 'P2': {'service', 'bucket_end', 'source_profile', 'queries_sha256', 'pod_identity_verified'},
                'P3': {'service', 'bucket_end', 'coverage', 'lag_seconds', 'eligible_for_inference', 'observations'},
                'P4': {'service', 'bucket_end', 'prometheus_count', 'server_completions', 'relative_difference',
                       'error_count', 'failed_requests'},
                'P5': {'service', 'bucket_end', 'prometheus_mean_ms', 'exact_mean_ms', 'relative_difference'},
                'P6': {'service', 'window_end', 'features', 'scores'}}
    for name, keys in required.items():
        assert keys <= recorded.get(name, set()), (name, keys - recorded.get(name, set()))


def test_release_refuses_a_checkout_whose_alibaba_config_differs_from_the_bundle(tmp_path):
    sha, manifest, report, controls, record, verification = evidence(tmp_path)
    other = dict(manifest['provenance']['config'], window_minutes=manifest['provenance']['config']['window_minutes'] + 1)
    with pytest.raises(ValueError):
        validate_evidence(manifest, report, controls, record, verification, sha, scores_for=scorer(tmp_path, sha),
                          repository_config=other)


def test_parity_records_a_quarantined_capture_as_fail_instead_of_crashing(tmp_path, monkeypatch):
    # Codex review of bbe4731: a capture that the pipeline quarantines left no observation, and
    # next(...) raised StopIteration before parity.json/verification.json were written.
    import importlib
    from datetime import datetime, timezone
    from sentinelops.ml.models import load_bundle
    script = importlib.import_module('scripts.check_alibaba_source_parity')
    sha, *_ = evidence(tmp_path)
    manifest, detector, risk = load_bundle(tmp_path / 'bundle', expected_manifest_sha256=sha)
    monkeypatch.setattr(script, 'process_captures', lambda captures, store: {
        'observation_refs': [], 'observation_count': 0, 'quarantine_refs': ['artifact://quarantine/x.json'],
        'manifest_ref': 'artifact://manifests/m.json'})
    ends = [datetime(2026, 11, 9, 2, 20, tzinfo=timezone.utc)]
    checks, failures, _ = script.evaluate(tmp_path, (manifest, detector, risk, True), [], [], ends,
                                          {'maximum_lag_seconds': 60})
    assert {f['criterion'] for f in failures} >= {'P3', 'P4', 'P5', 'P6'}
    assert all(item['ok'] is False for item in checks['P4'])


def test_p6_features_must_follow_from_the_measurements(tmp_path):
    # Codex review of d9270fd: replace P6 features by another finite vector and rescore it with
    # the bundle; P2–P5 unchanged. The validator recomputes features from P4/P5 and rejects it.
    sha, manifest, report, controls, record, verification = evidence(tmp_path)
    v = copy.deepcopy(verification)
    config, score = manifest['provenance']['config'], scorer(tmp_path, sha)
    item = v['criteria']['P6'][0]
    item['features'] = {name: value + 0.25 for name, value in item['features'].items()}
    item['scores'] = score([item['features'][name] for name in config['features']])
    r = dict(record, evidence=[parity_verification_ref(v)])
    with pytest.raises(ValueError):
        validate_evidence(manifest, report, controls, r, v, sha, scores_for=score, repository_config=config)


def test_capture_with_a_failed_prometheus_query_reaches_evaluate_instead_of_crashing():
    # Codex review of d9270fd: a query error left no data, and capture_buckets raised KeyError.
    import asyncio
    import importlib
    from datetime import datetime, timezone
    script = importlib.import_module('scripts.check_alibaba_source_parity')

    class Source:
        endpoint = 'http://unused'

        def capture(self, service, end, profile):
            return {'service': service, 'bucket_end': '2026-11-09T02:20:00Z',
                    'queries': {'requests': {'response': {'status': 'error', 'error': 'timeout'}}}}
    end = datetime(2026, 11, 9, 2, 20, tzinfo=timezone.utc)
    captures = asyncio.run(script.capture_buckets(None, Source(), [end], {}))
    assert len(captures) == len(script.SERVICES)


def test_pod_identity_failure_is_recorded_not_raised():
    import asyncio
    import importlib
    from datetime import datetime, timezone
    import httpx
    script = importlib.import_module('scripts.check_alibaba_source_parity')

    class Source:
        endpoint = 'http://prometheus.invalid'

        def capture(self, service, end, profile):
            return {'service': service, 'bucket_end': '2026-11-09T02:20:00Z', 'queries': {'requests': {'response': {
                'status': 'success', 'data': {'result': [{'value': [0, '60']}]}}}}}

    class Client:
        async def get(self, *args, **kwargs):
            raise httpx.ConnectError('fixture: Prometheus unreachable for the pod query')
    end = datetime(2026, 11, 9, 2, 20, tzinfo=timezone.utc)
    captures = asyncio.run(script.capture_buckets(Client(), Source(), [end], {}))
    assert [c['pod_identity_verified'] for c in captures] == [False] * len(script.SERVICES)


def test_parity_must_have_exactly_the_scripts_minute_count(tmp_path):
    # Codex review of af37e59: 12 consecutive minutes (one more than minute_count) cannot come from
    # the script, so they must not certify the bundle even when every derived value is consistent.
    import importlib
    script = importlib.import_module('scripts.check_alibaba_source_parity')
    sha, manifest, report, controls, record, verification = evidence(tmp_path)
    config, score = manifest['provenance']['config'], scorer(tmp_path, sha)
    assert script.minute_count(config) == len(BUCKETS)
    longer = criteria(manifest['model_version'], config, score, buckets=BUCKETS + ('2026-11-09T02:31:00Z',))
    v = dict(verification, criteria=longer)
    r = dict(record, evidence=[parity_verification_ref(v)])
    with pytest.raises(ValueError):
        validate_evidence(manifest, report, controls, r, v, sha, scores_for=score, repository_config=config)


def test_a_5xx_during_the_drive_is_recorded_as_a_p4_failure():
    # Codex review of af37e59: one non-200 response raised out of drive() before evidence was written.
    import asyncio
    import importlib
    from datetime import timedelta
    import httpx
    script = importlib.import_module('scripts.check_alibaba_source_parity')
    responses = iter([500, 200, 200] * 3)

    class Client:
        async def request(self, method, url, json=None, headers=None):
            trace = headers['traceparent'].split('-')[1]
            return httpx.Response(next(responses), headers={'x-trace-id': trace,
                                                            'x-correlation-id': headers['x-correlation-id']})
    targets = {s: 'http://' + s for s in script.SERVICES}
    rows = asyncio.run(script.drive(Client(), targets, script.utcnow() + timedelta(milliseconds=50)))
    assert any(row['failed'] for row in rows) and not all(row['failed'] for row in rows)



def run_parity_main(tmp_path, monkeypatch, *, bundle_ok=True, verify_bundle=None, deploy=None, ready=None,
                    configuration=None, inventory=None, cleanup=None, measure=None):
    """Drive check_alibaba_source_parity.main with every sandbox/HTTP edge mocked: no Docker, network or port."""
    import importlib
    import subprocess
    import sys
    script = importlib.import_module('scripts.check_alibaba_source_parity')
    bundle, output = tmp_path / 'bundle', tmp_path / 'output'
    bundle.mkdir()
    manifest = {'model_version': 'fixture-model', 'provenance': {'config': {'fixture': True}}}
    started = []

    class Sandbox:
        name = 'fixture-sandbox'
        kubeconfig = tmp_path / 'missing-kubeconfig'

        def __init__(self, root, kind):
            started.append(kind)

        def forward(self, service, port):
            return 'http://fixture/' + service

        def inventory(self):
            return (inventory or (lambda: {}))()

        def cleanup(self):
            (cleanup or (lambda: None))()

    class Source:
        endpoint = 'http://fixture/prometheus'

        def __init__(self, endpoint):
            pass

        def verify_configuration(self):
            return (configuration or (lambda: {'configuration_hash': 'c' * 64, 'scrape_interval': '5s'}))()

        def close(self):
            pass

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def always_ready(*args):
        pass

    monkeypatch.setattr(script, 'clean_commit', lambda: 'a' * 40)
    monkeypatch.setattr(script, 'verify_bundle', verify_bundle or (lambda *args: (manifest, None, None, bundle_ok)))
    monkeypatch.setattr(script, 'BusinessSandbox', Sandbox)
    monkeypatch.setattr(script, 'PrometheusSource', Source)
    monkeypatch.setattr(script.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(script, 'wait_ready', ready or always_ready)
    monkeypatch.setattr(script, 'deploy', deploy or (lambda sandbox: [{'image': 'fixture'}]))
    monkeypatch.setattr(script.signal, 'signal', lambda *args: None)
    if measure is not None:
        monkeypatch.setattr(script, 'measure', measure)
    monkeypatch.setattr(sys, 'argv', ['check_alibaba_source_parity.py', '--kind', 'unused', '--bundle', str(bundle),
                                      '--manifest-sha256', 'b' * 64, '--environment', 'test', '--verified-by', 'tester',
                                      '--output', str(output)])
    with pytest.raises((SystemExit, Exception, KeyboardInterrupt)) as exited:
        script.main()
    if isinstance(exited.value, SystemExit):
        assert exited.value.code not in (0, None)
    verification = json.loads((output / 'verification.json').read_text())
    record = json.loads((output / 'parity.json').read_text())
    assert record['status'] == 'FAIL' and record['evidence'] == [parity_verification_ref(verification)]
    assert verification['failures'] and verification['activation_allowed'] is False
    return verification, started, subprocess


def test_parity_main_writes_p1_failure_before_starting_a_sandbox(tmp_path, monkeypatch):
    verification, started, _ = run_parity_main(tmp_path, monkeypatch, bundle_ok=False)
    assert started == []
    assert verification['failures'] == [{'criterion': 'P1', 'reason': 'bundle verification failed; no sandbox started'}]
    assert verification['criteria']['P1'] == [{'ok': False, 'reason': 'bundle verification failed; no sandbox started',
                                               'model_version': 'fixture-model'}]


def test_parity_main_writes_p1_failure_when_the_bundle_cannot_be_loaded(tmp_path, monkeypatch):
    def unloadable(*args):
        raise ValueError('manifest hash mismatch')
    verification, started, _ = run_parity_main(tmp_path, monkeypatch, verify_bundle=unloadable)
    assert started == [] and verification['candidate_id'] is None and verification['config_sha256'] is None
    assert verification['failures'] == [{'criterion': 'P1', 'reason': 'bundle verification failed: '
                                         'ValueError; no sandbox started'}]
    assert 'manifest hash mismatch' not in json.dumps(verification)


def _raise(message):
    def fail(*args):
        raise RuntimeError(message)
    return fail


async def _not_ready(*args):
    raise RuntimeError('fixture readiness failure')


@pytest.mark.parametrize(('override', 'reason'), [
    ({'ready': _not_ready}, 'readiness check failed: RuntimeError'),
    ({'configuration': _raise('scrape interval 15s')}, 'Prometheus configuration preflight failed: RuntimeError'),
    ({'inventory': _raise('0/2 pods ready')}, 'sandbox inventory preflight failed: RuntimeError'),
])
def test_parity_main_writes_p2_failure_when_preflight_fails(tmp_path, monkeypatch, override, reason):
    verification, _, _ = run_parity_main(tmp_path, monkeypatch, **override)
    assert verification['failures'] == [{'criterion': 'P2', 'reason': reason}]
    assert verification['criteria'] == {'P2': [{'ok': False, 'reason': reason}]}


def test_parity_main_writes_p2_failure_when_deployment_fails(tmp_path, monkeypatch):
    import subprocess

    def broken(sandbox):
        raise subprocess.CalledProcessError(1, ['/usr/bin/kind', 'create', 'cluster', '--secret-arg'])
    verification, _, _ = run_parity_main(tmp_path, monkeypatch, deploy=broken)
    assert verification['failures'] == [{'criterion': 'P2', 'reason': 'run aborted: command kind exited 1'}]
    assert '--secret-arg' not in json.dumps(verification)


def test_parity_evidence_never_carries_a_timed_out_command_line(tmp_path, monkeypatch):
    import subprocess

    def hung(sandbox):
        raise subprocess.TimeoutExpired(['/usr/bin/kubectl', '--token=SECRET', 'apply'], 30)
    verification, _, _ = run_parity_main(tmp_path, monkeypatch, deploy=hung)
    assert verification['failures'] == [{'criterion': 'P2', 'reason': 'run aborted: command kubectl timed out'}]
    assert 'SECRET' not in json.dumps(verification)


def test_a_cleanup_failure_after_a_clean_measurement_is_recorded_as_fail(tmp_path, monkeypatch):
    checks = {'P1': [{'ok': True, 'model_version': 'fixture-model'}]}
    config = {'configuration_hash': 'c' * 64, 'scrape_interval': '5s'}

    async def measured(*args):
        return checks, [], 'artifact://manifests/' + 'd' * 64 + '.json', config

    def cleanup():
        raise ValueError('node identity changed')
    verification, _, _ = run_parity_main(tmp_path, monkeypatch, measure=measured, cleanup=cleanup)
    assert verification['failures'] == [{'criterion': 'P2', 'reason': 'run aborted: ValueError'}]
    assert verification['criteria'] == checks and verification['configuration_hash'] == 'c' * 64


def test_an_interrupt_before_the_bundle_is_checked_still_leaves_p1_evidence(tmp_path, monkeypatch):
    def interrupted(*args):
        raise KeyboardInterrupt
    verification, started, _ = run_parity_main(tmp_path, monkeypatch, verify_bundle=interrupted)
    assert started == []
    assert verification['failures'] == [{'criterion': 'P1', 'reason': 'run aborted: KeyboardInterrupt'}]
