"""ADR-016 release builder: evidence must bind the exact bundle; nothing is activated."""
import copy
import json

import pytest

pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("xgboost")

from sentinelops.ml.alibaba_bundle import package_fit
from sentinelops.ml.alibaba_release import CRITERIA, build_alibaba_release, validate_evidence
from sentinelops.ml.models import read_bundle_manifest
from sentinelops.ml.registry import ModelRegistry
from sentinelops.ml.release import parity_verification_ref
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id
from tests.test_alibaba_bundle import synthetic_fit


def evidence(tmp_path):
    fit = synthetic_fit(tmp_path)
    report = json.loads((fit / 'report.json').read_text())
    report['counts'] = {k: {'services': 3} for k in ('train', 'calibration', 'evaluation')}
    (fit / 'report.json').write_text(json.dumps(report, default=float))
    result = package_fit(fit, tmp_path / 'bundle')
    manifest = read_bundle_manifest(tmp_path / 'bundle', expected_manifest_sha256=result['manifest_sha256'])
    report = json.loads((tmp_path / 'bundle' / 'report.json').read_text())
    controls = {'report_version': 'alibaba-relative-controls/v1', 'fit_report_sha256': stable_id(report),
                'config_sha256': report['config_sha256'], 'no_refit_or_retune': True}
    scope = {'candidate_id': manifest['model_version'], 'environment': 'test', 'source_profile': 'prometheus-increase-60s-5s/v2',
             'models': [{'service': s, 'model_version': manifest['model_version']} for s in sorted(BUSINESS_ROUTES)],
             'verified_by': 'test', 'verified_at': '2026-11-09T00:00:00Z'}
    verification = {'version': 'model-source-parity-check/v1', 'criteria_version': 'alibaba-parity-criteria/v1', **scope,
                    'bundle_manifest_sha256': result['manifest_sha256'], 'failures': [],
                    'criteria': {name: [{'ok': True}] for name in CRITERIA}}
    record = {'parity_version': 'model-source-parity/v1', **scope, 'status': 'PASS',
              'evidence': [parity_verification_ref(verification)]}
    return result['manifest_sha256'], manifest, report, controls, record, verification


def test_evidence_accepts_exact_bundle_and_rejects_drift(tmp_path):
    sha, manifest, report, controls, record, verification = evidence(tmp_path)
    validate_evidence(manifest, report, controls, record, verification, sha)
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
            validate_evidence(manifest, report, c, r, v, sha)


def test_release_requires_an_explicit_owner_decision(tmp_path):
    sha, _, _, controls, record, verification = evidence(tmp_path)
    with pytest.raises(ValueError):
        build_alibaba_release(tmp_path / 'bundle', sha, controls=controls, parity_record=record,
                              parity_verification=verification, approved_by=' ', decision_note='x',
                              output=tmp_path / 'release')
    assert not (tmp_path / 'release').exists()


@pytest.mark.skipif(not hasattr(ModelRegistry(), 'is_alibaba'), reason='registry v3 consumer not merged yet')
def test_release_loads_as_one_shared_alibaba_registry(tmp_path):
    sha, manifest, _, controls, record, verification = evidence(tmp_path)
    result = build_alibaba_release(tmp_path / 'bundle', sha, controls=controls, parity_record=record,
                                   parity_verification=verification, approved_by='owner', decision_note='G3 accepted',
                                   output=tmp_path / 'release')
    assert result['activation_performed'] is False and result['model_version'] == manifest['model_version']
