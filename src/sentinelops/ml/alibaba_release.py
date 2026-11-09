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
import os
from pathlib import Path

from sentinelops.ml.alibaba_bundle import BUNDLE_VERSION, ORIGIN
from sentinelops.ml.release import APPROVAL_VERSION, PARITY_VERIFICATION_VERSION, PARITY_VERSION, RELEASE_VERSION, \
    parity_verification_ref
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2
from sentinelops.telemetry.store import ArtifactStore

PARITY_CRITERIA = 'alibaba-parity-criteria/v1'
CRITERIA = ('P1', 'P2', 'P3', 'P4', 'P5', 'P6')
BUNDLE_DIRECTORY = 'alibaba-v2022'


def _pinned(path, sha256):
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError(f'{Path(path).name} differs from its pin')
    return data


def validate_evidence(manifest, report, controls, parity_record, parity_verification, manifest_sha256):
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
            or set(criteria) != set(CRITERIA)
            or not all(item['ok'] for name in CRITERIA for item in criteria[name])):
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
    validate_evidence(manifest, report, controls, parity_record, parity_verification, manifest_sha256)
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
