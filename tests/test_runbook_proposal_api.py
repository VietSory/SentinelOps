"""HTTP authentication/CAS and real immutable approval/controller handoff."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from sentinelops.api import create_app
from sentinelops.api_settings import Settings
from sentinelops.remediation import approvals
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore

OPERATOR, PRODUCER = 'operator-' + 'x' * 40, 'producer-' + 'x' * 40


def fixture(tmp_path, **changes):
    now = datetime.now(timezone.utc)
    proposal = {'proposal_version': approvals.PROPOSAL_VERSION, 'proposal_id': 'a' * 64,
        'incident_id': 'INC-review', 'incident_revision': 2, 'runbook_id': 'payment-bad-deploy-rollback',
        'catalog_sha256': 'c' * 64, 'executor_runbook': 'payment-rollback-known-good/v1',
        'decision': 'oncall', 'score': .95, 'margin': .4, 'symptoms': {'severity': 'high'},
        'created_at': timestamp(now - timedelta(seconds=1)), 'expires_at': timestamp(now + timedelta(minutes=14)),
        'current_image': 'registry.invalid/payment@sha256:' + '1' * 64,
        'rollback_image': 'registry.invalid/payment@sha256:' + '2' * 64, **changes}
    store = ArtifactStore(tmp_path)
    reference = store.write('manifests', proposal)
    class Repository:
        revision = 2
        async def check_ready(self): pass
        async def get(self, identifier, revision=None):
            assert identifier == proposal['incident_id']
            return {'incident_id': identifier, 'revision': revision or self.revision,
                'payload': {'incident_id': identifier, 'severity': 'high', 'root_cause_ranking': [
                    {'service': s, 'score': v, 'evidence_refs': []}
                    for s, v in [('payment-service', .9), ('checkout-service', .3), ('inventory-service', .1)]]}}
    repo = Repository()
    client = TestClient(create_app(Settings(environment='test', operator_token=OPERATOR, api_token=PRODUCER,
        runbook_proposal_store=str(tmp_path)), repository=repo))
    path = '/v1/runbook-proposals/' + proposal['proposal_id']
    headers = {'Authorization': 'Bearer ' + OPERATOR, 'If-Match': '"' + stable_id(proposal) + '"'}
    return SimpleNamespace(store=store, ref=reference, proposal=proposal, repo=repo, client=client, path=path, headers=headers)


def decide(f, decision='approve', **kwargs):
    return f.client.post(f.path + '/decision', headers=kwargs.pop('headers', f.headers),
        json=kwargs.pop('body', {'decision': decision, 'reason': 'Đã xem bằng chứng'}), **kwargs)


def test_list_detail_pin_incident_and_proposal_hash_without_writes(tmp_path):
    f = fixture(tmp_path)
    before = sorted(p.name for p in (tmp_path / 'manifests').iterdir())
    response = f.client.get(f.path, headers=f.headers)
    assert response.status_code == 200 and response.headers['etag'] == f.headers['If-Match']
    result = response.json()
    assert result['proposal'] == f.proposal and result['incident']['severity'] == 'high'
    assert len(result['incident']['root_cause_ranking']) == 3 and result['status'] == 'pending'
    assert f.client.get('/v1/runbook-proposals?incident_id=other', headers=f.headers).json()['items'] == []
    assert f.client.get('/v1/runbook-proposals', headers=f.headers).json()['items'] == [result]
    assert sorted(p.name for p in (tmp_path / 'manifests').iterdir()) == before
    assert not (tmp_path / 'runbook-approvals').exists()


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_decision_uses_server_actor_shared_store_and_cli_receipt_format(tmp_path, monkeypatch, decision):
    f = fixture(tmp_path)
    real = approvals.record_approval
    calls = []
    def recording(store, **kwargs):
        calls.append((store.root, kwargs))
        return real(store, **kwargs)
    monkeypatch.setattr(approvals, 'record_approval', recording)
    response = decide(f, decision)
    assert response.status_code == 201
    receipt = approvals.read_slot(f.store, 'approval', f.proposal['proposal_id'])
    assert set(receipt) == {'approval_version', 'proposal_id', 'proposal_sha256', 'approved_by',
                            'approved_at', 'decision', 'reason'}
    assert receipt['approved_by'].startswith('local-operator:') and OPERATOR not in response.text
    assert receipt['decision'] == decision and receipt['proposal_sha256'] == stable_id(f.proposal)
    assert calls[0][0] == tmp_path.resolve() and calls[0][1]['confirm_oncall_approval'] is True
    assert response.json()['status'] == ('approved' if decision == 'approve' else 'engineer')
    before = (tmp_path / 'runbook-approvals/approval' / (f.proposal['proposal_id'] + '.json')).read_bytes()
    assert decide(f, 'reject' if decision == 'approve' else 'approve').status_code == 409
    assert (tmp_path / 'runbook-approvals/approval' / (f.proposal['proposal_id'] + '.json')).read_bytes() == before


@pytest.mark.parametrize('token,status', [(None, 401), ('bad', 401), (PRODUCER, 403)])
def test_only_operator_can_read_or_decide_proposals(tmp_path, token, status):
    f = fixture(tmp_path)
    headers = {'If-Match': f.headers['If-Match']}
    if token: headers['Authorization'] = 'Bearer ' + token
    assert f.client.get('/v1/runbook-proposals', headers=headers).status_code == status
    assert f.client.get(f.path, headers=headers).status_code == status
    assert decide(f, headers=headers).status_code == status
    assert approvals.read_slot(f.store, 'approval', f.proposal['proposal_id']) is None


@pytest.mark.parametrize('patch', [{'approved_by': 'forged'}, {'approved': True}, {'rollback_image': 'evil'},
    {'decision': 'execute'}, {'reason': ''}, {'reason': ' '}, {'reason': 'x' * 2001}, {'reason': False}])
def test_client_authority_and_invalid_payloads_never_create_receipts(tmp_path, patch):
    f = fixture(tmp_path)
    assert decide(f, body={'decision': 'approve', 'reason': 'reviewed', **patch}).status_code == 422
    assert approvals.read_slot(f.store, 'approval', f.proposal['proposal_id']) is None


@pytest.mark.parametrize('etag,status', [(None, 428), ('*', 422), ('W/"' + 'a' * 64 + '"', 422),
    ('"2"', 422), ('"' + '0' * 64 + '"', 412)])
def test_if_match_requires_exact_immutable_proposal_hash(tmp_path, etag, status):
    f = fixture(tmp_path)
    headers = {'Authorization': 'Bearer ' + OPERATOR}
    if etag is not None: headers['If-Match'] = etag
    assert decide(f, headers=headers).status_code == status
    assert approvals.read_slot(f.store, 'approval', f.proposal['proposal_id']) is None


def test_stale_expired_future_consumed_and_terminal_proposals_fail_closed(tmp_path):
    for index, failure in enumerate(('stale', 'expired', 'future', 'used', 'outcome')):
        now = datetime.now(timezone.utc)
        changes = {'expires_at': timestamp(now - timedelta(seconds=1))} if failure == 'expired' else (
            {'created_at': timestamp(now + timedelta(minutes=1))} if failure == 'future' else {})
        f = fixture(tmp_path / str(index), **changes)
        if failure == 'stale': f.repo.revision = 3
        if failure in ('used', 'outcome'): approvals.create_slot(f.store, failure, f.proposal['proposal_id'], {'handled': True})
        assert decide(f).status_code == 409
        assert approvals.read_slot(f.store, 'approval', f.proposal['proposal_id']) is None


def test_parallel_approve_reject_is_create_only(tmp_path):
    f = fixture(tmp_path)
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda decision: decide(f, decision), ['approve', 'reject']))
    assert sorted(r.status_code for r in responses) == [201, 409]
    receipt = approvals.read_slot(f.store, 'approval', f.proposal['proposal_id'])
    assert receipt['decision'] in ('approve', 'reject')


def test_missing_unknown_or_corrupt_store_never_exposes_path_or_creates_it(tmp_path):
    f = fixture(tmp_path)
    assert f.client.get('/v1/runbook-proposals/' + 'b' * 64, headers=f.headers).status_code == 404
    assert f.client.get('/v1/runbook-proposals/not-a-hash', headers=f.headers).status_code == 422
    path = tmp_path / 'manifests' / f.ref.rsplit('/', 1)[1]
    path.write_text('{"secret":"DO-NOT-EXPOSE"}')
    response = f.client.get(f.path, headers=f.headers)
    assert response.status_code == 503 and 'DO-NOT-EXPOSE' not in response.text
    for root in (None, str(tmp_path / 'not-mounted')):
        client = TestClient(create_app(Settings(operator_token=OPERATOR, runbook_proposal_store=root), repository=f.repo))
        assert client.get('/v1/runbook-proposals', headers=f.headers).status_code == 503
    assert not (tmp_path / 'not-mounted').exists()


def test_static_tokens_remain_local_test_only_and_operator_cannot_write_incidents(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='local/test'):
        Settings(environment='aws-dev', operator_token=OPERATOR, runbook_proposal_store=str(tmp_path))
    monkeypatch.setenv('SENTINELOPS_RUNBOOK_PROPOSAL_STORE', str(tmp_path))
    assert Settings.from_env().runbook_proposal_store == str(tmp_path)
    f = fixture(tmp_path)
    assert f.client.post('/v1/incidents', headers=f.headers, json={}).status_code == 403


def test_oidc_decision_actor_is_verified_subject_and_producer_is_denied(tmp_path):
    from sentinelops.oidc import OidcConfig
    f = fixture(tmp_path)
    config = OidcConfig(issuer='https://identity.invalid', audience='sentinelops', jwks_url='https://identity.invalid/jwks')
    class Verifier:
        def __init__(self): self.config = config
        async def identify(self, token, *, required_scope=None):
            from sentinelops.oidc import ScopeMissing
            if token != OPERATOR: raise ScopeMissing('required scope missing')
            return ('approver' if required_scope == config.approval_scope else 'operator', 'verified-subject')
    client = TestClient(create_app(Settings(environment='aws-dev', oidc=config,
        runbook_proposal_store=str(tmp_path)), repository=f.repo, identity_verifier=Verifier()))
    body = {'decision': 'approve', 'reason': 'reviewed'}
    assert client.post(f.path + '/decision', headers={**f.headers, 'Authorization': 'Bearer ' + PRODUCER}, json=body).status_code == 403
    result = client.post(f.path + '/decision', headers=f.headers, json=body)
    assert result.status_code == 201
    assert result.json()['approval']['approved_by'] == 'oidc-approver:verified-subject'


def test_signed_cognito_scope_read_cannot_approve_but_explicit_approval_scope_can(tmp_path):
    from test_oidc import verifier, token
    f = fixture(tmp_path)
    identity = verifier()
    client = TestClient(create_app(Settings(environment='aws-dev', oidc=identity.config,
        runbook_proposal_store=str(tmp_path)), repository=f.repo, identity_verifier=identity))
    read, approve = identity.config.operator_scope, identity.config.approval_scope
    for scopes in (read, 'sentinelops/incidents.write', read+' sentinelops/incidents.write', approve):
        headers = {**f.headers, 'Authorization': 'Bearer '+token(scopes)}
        result = client.post(f.path+'/decision', headers=headers, json={'decision':'reject','reason':'reviewed'})
        assert result.status_code == 403 and result.json()['code'] == 'approval_scope_required'
    assert approvals.read_slot(f.store, 'approval', f.proposal['proposal_id']) is None
    headers = {**f.headers, 'Authorization': 'Bearer '+token(read)}
    assert client.get(f.path, headers=headers).status_code == 200
    headers['Authorization'] = 'Bearer '+token(read+' '+approve)
    result = client.post(f.path+'/decision', headers=headers, json={'decision':'approve','reason':'reviewed'})
    assert result.status_code == 201 and result.json()['approval']['approved_by'] == 'oidc-approver:svc-producer'


@pytest.mark.parametrize('changes', [{'current_image': None}, {'rollback_image': None},
    {'rollback_image': 'registry.invalid/payment:latest'}])
def test_incomplete_image_review_cannot_be_approved_but_can_be_rejected(tmp_path, changes):
    f = fixture(tmp_path, **changes)
    assert f.client.get(f.path, headers=f.headers).json()['review_images'] is None
    response = decide(f)
    assert response.status_code == 409 and response.json()['code'] == 'proposal_review_incomplete'
    assert approvals.read_slot(f.store, 'approval', f.proposal['proposal_id']) is None
    assert decide(f, 'reject').status_code == 201


def test_http_reject_is_consumed_by_real_controller_without_dispatch(tmp_path, monkeypatch):
    from test_remediation_controller import fixture as controller_fixture
    c = controller_fixture(tmp_path, monkeypatch)
    asyncio.run(c.controller.tick())
    proposal = next(iter(c.controller._decisions.values()))
    monkeypatch.setattr(approvals, 'datetime', SimpleNamespace(now=lambda tz: c.f.clock()))
    class Repository:
        async def check_ready(self): pass
        async def get(self, identifier, revision=None): return deepcopy(c.snapshot)
    client = TestClient(create_app(Settings(operator_token=OPERATOR, runbook_proposal_store=str(c.store.root)),
                                   repository=Repository()))
    response = client.post('/v1/runbook-proposals/' + proposal['proposal_id'] + '/decision',
        headers={'Authorization': 'Bearer ' + OPERATOR, 'If-Match': '"' + stable_id(proposal) + '"'},
        json={'decision': 'reject', 'reason': 'Chuyển engineer'})
    assert response.status_code == 201
    asyncio.run(c.controller.tick())
    outcome = approvals.read_slot(c.store, 'outcome', proposal['proposal_id'])
    assert outcome['phase'] == 'escalated' and outcome['reason'] == 'approval-rejected'
    assert c.f.kube.calls == []
