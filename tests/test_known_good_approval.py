"""Operator known-good ledger; fixtures here are never an approval in any store."""
import asyncio
from datetime import timedelta

import pytest

from sentinelops.remediation.approval import build_known_good_ledger
from sentinelops.remediation.domain import Blocked
from sentinelops.remediation.registry import KnownGoodRegistry
from sentinelops.telemetry.core import stable_id, timestamp
from test_remediation_kubernetes import TARGET
from test_remediation_registry import approved_fixture
from test_remediation_verification import START


def evidence(store, record, source):
    item = record['images'][0]
    review = store.write('manifests', {'review_version': 'fixture'})
    value = {'evidence_version': 'payment-image-evidence/v1', 'review_ref': review, 'target': item['target'],
             'image': item['image'], 'environment': 'fixture', 'health_stream_id': source.stream_id,
             'health_policy_sha256': stable_id(source.policy), 'opened_at': timestamp(START),
             'completed_at': timestamp(START + timedelta(minutes=6)), 'before_ref': item['before_ref'],
             'after_ref': item['after_ref'], 'health_ref': item['health_ref'], 'known_good_approved': False,
             'physical_binding_verified': False, 'action_dispatch_authorized': False}
    return store.write('manifests', value)


def test_ledger_from_evidence_resolves_through_the_executor_registry(tmp_path):
    store, source, record = approved_fixture(tmp_path)
    ref, ledger = build_known_good_ledger(store, evidence(store, record, source), approved_by='operator-a',
                                          environment='fixture', now=START + timedelta(minutes=7))
    assert ledger['action_dispatch_authorized'] is False and ledger['approval_scope'] == 'known-good-image-only'
    registry = KnownGoodRegistry(store, ref, approved_by='operator-a', source=source, environment='fixture')
    result = asyncio.run(registry.resolve(TARGET, incident_at=START + timedelta(minutes=8)))
    assert result.image == record['images'][0]['image']
    with pytest.raises(Blocked, match='before-incident'):
        asyncio.run(registry.resolve(TARGET, incident_at=START + timedelta(minutes=7)))


def test_approval_cannot_precede_evidence_or_omit_operator(tmp_path):
    store, source, record = approved_fixture(tmp_path)
    ref = evidence(store, record, source)
    with pytest.raises(ValueError, match='precede'):
        build_known_good_ledger(store, ref, approved_by='operator-a', environment='fixture', now=START + timedelta(minutes=5))
    with pytest.raises(ValueError, match='operator'):
        build_known_good_ledger(store, ref, approved_by=' ', environment='fixture', now=START + timedelta(minutes=7))
    with pytest.raises(ValueError, match='environment'):
        build_known_good_ledger(store, ref, approved_by='operator-a', environment='local', now=START + timedelta(minutes=7))
