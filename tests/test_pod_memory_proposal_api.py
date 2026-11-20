"""The OOM action shares authenticated decisions, never image-rollback authority."""
from dataclasses import asdict

import pytest

from sentinelops.remediation import approvals
from sentinelops.remediation.pod_memory import MemoryTarget, RUNBOOK, write_state
from test_runbook_proposal_api import fixture, decide


def memory_fixture(tmp_path, **changes):
    image = 'registry.invalid/oom-demo@sha256:' + '1' * 64
    return fixture(tmp_path, executor_runbook=RUNBOOK, runbook_id='pod-oom-memory-limit',
        target=asdict(MemoryTarget('cluster', 'namespace', 'deployment', image)),
        current_image=image, rollback_image=None, cause='OOMKilled', pod_uid='failed-pod',
        resource_change={'memory_before': '64Mi', 'memory_after': '256Mi'}, **changes)


def test_memory_review_pins_limit_image_and_failed_pod_before_approval(tmp_path):
    f = memory_fixture(tmp_path)
    view = f.client.get(f.path, headers=f.headers).json()
    assert view['status'] == 'pending' and view['review_images'] is None
    assert view['review_change'] == {'kind': 'memory_limit', 'memory_before': '64Mi',
        'memory_after': '256Mi', 'cause': 'OOMKilled', 'pod_uid': 'failed-pod',
        'image': f.proposal['current_image'], 'deployment': 'sentinelops-oom-demo', 'container': 'oom-demo'}
    result = decide(f)
    assert result.status_code == 201
    assert result.json()['status'] == 'approved'
    assert result.json()['approval']['proposal_sha256'] == view['proposal_sha256']
    assert not (tmp_path / 'runbook-approvals' / 'used').exists()


@pytest.mark.parametrize('change', [
    {'resource_change': {'memory_before': '64Mi', 'memory_after': '512Mi'}},
    {'pod_uid': ''}, {'cause': 'Error'}, {'target': {'deployment': 'payment'}}])
def test_malformed_memory_review_never_falls_back_to_valid_rollback_images(tmp_path, change):
    f = memory_fixture(tmp_path)
    # Replace the immutable proposal before the operator first reads it.
    from sentinelops.telemetry.core import stable_id
    f.proposal.update(change, rollback_image='registry.invalid/old@sha256:' + '2' * 64)
    old = tmp_path / 'manifests' / f.ref.rsplit('/', 1)[1]
    old.unlink()
    f.store.write('manifests', f.proposal)
    f.headers['If-Match'] = '"' + stable_id(f.proposal) + '"'
    view = f.client.get(f.path, headers=f.headers).json()
    assert view['review_change'] is None and view['review_images'] is not None
    result = decide(f)
    assert result.status_code == 409 and result.json()['code'] == 'proposal_review_incomplete'
    assert approvals.read_slot(f.store, 'approval', f.proposal['proposal_id']) is None
    assert decide(f, 'reject').status_code == 201


@pytest.mark.parametrize('phase', ['dispatching', 'verifying', 'recovered', 'blocked', 'escalated', 'rejected'])
def test_memory_controller_state_survives_incident_revision_advancement(tmp_path, phase):
    f = memory_fixture(tmp_path)
    write_state(f.store, {'proposal_id': f.proposal['proposal_id'], 'runbook': RUNBOOK,
        'phase': phase, 'reason': 'controller evidence', 'successful_probes': 31})
    f.repo.revision = 7
    result = f.client.get(f.path, headers=f.headers)
    assert result.status_code == 200
    view = result.json()
    assert view['status'] == phase and view['current_revision'] == 7
    assert view['memory_action']['successful_probes'] == 31
    assert decide(f).status_code == 409


def test_controller_blocked_memory_action_cannot_receive_a_late_decision(tmp_path):
    f = memory_fixture(tmp_path)
    write_state(f.store, {'proposal_id': f.proposal['proposal_id'], 'runbook': RUNBOOK,
        'phase': 'blocked', 'reason': 'target changed'})
    assert decide(f).status_code == 409
    assert approvals.read_slot(f.store, 'approval', f.proposal['proposal_id']) is None
