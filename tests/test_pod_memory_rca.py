"""RCA for the bounded Kubernetes OOM demo."""
import asyncio
from datetime import timedelta

import pytest

from sentinelops.contracts import validate_payload
from sentinelops.explanations.service import ExplanationProvider, explain_incident
from sentinelops.remediation.pod_memory import AFTER, MemoryController, project
from test_pod_memory import Repository, approve, fixture, inventory


SERVICE = 'sentinelops-oom-demo'
EVIDENCE_ID = 'kubernetes-oom'


def verified_fixture(tmp_path):
    setup = fixture(tmp_path)
    now, target, _, kube, *_ = setup
    kube.observation = project(target, *inventory(target, now[0]), now=now[0])
    return setup


def envelope(snapshot, recorded_at):
    return {
        **snapshot,
        'schema_version': '1.0',
        'recorded_at': recorded_at,
        'actor': 'pod-memory-demo-controller',
        'reason': 'test-snapshot',
        'timeline': {'events': [], 'next_after_revision': None},
    }


def test_verified_oom_writes_one_evidence_bound_candidate(tmp_path):
    async def scenario():
        _, _, _, _, repo, _, _, prepare = verified_fixture(tmp_path)
        proposal = await prepare()
        snapshot = await repo.get(proposal['incident_id'])
        assert snapshot['context']['lifecycle_stage'] == 'OPEN'
        validate_payload('incident-envelope.schema.json', envelope(snapshot, proposal['created_at']))

        payload = snapshot['payload']
        candidate, = payload['root_cause_ranking']
        assert candidate['service'] == SERVICE
        assert candidate['evidence_refs'] == [EVIDENCE_ID]
        evidence, = [item for item in payload['evidence'] if item['id'] == EVIDENCE_ID]
        assert evidence['service'] == SERVICE
        assert evidence['source_ref'] == proposal['observation_ref']

    asyncio.run(scenario())


@pytest.mark.parametrize(('decision', 'expected_stages'), [
    ('reject', {'ESCALATED'}),
    ('approve', {'VERIFYING', 'CLOSED'}),
])
def test_oom_candidate_survives_later_revisions(tmp_path, decision, expected_stages):
    async def scenario():
        now, _, store, _, repo, kwargs, controller, prepare = verified_fixture(tmp_path)
        proposal = await prepare()
        initial = await repo.get(proposal['incident_id'])
        approve(store, proposal, now[0], decision)
        state = await controller.tick()
        if decision == 'approve':
            controller = MemoryController(**kwargs)
            for _ in range(15):
                now[0] += timedelta(seconds=2)
                state = await controller.tick()

        assert state['finalized']
        later = [snapshot for snapshot in repo.history if snapshot['revision'] > initial['revision']]
        assert expected_stages <= {snapshot['context']['lifecycle_stage'] for snapshot in later}
        ranking = initial['payload']['root_cause_ranking']
        assert len(ranking) == 1
        for snapshot in later:
            assert snapshot['payload']['root_cause_ranking'] == ranking
            assert EVIDENCE_ID in {item['id'] for item in snapshot['payload']['evidence']}

    asyncio.run(scenario())


@pytest.mark.parametrize(('field', 'value'), [('reason', 'Error'), ('exitCode', 1)])
def test_oom_termination_requires_both_reason_and_exit_code(tmp_path, field, value):
    async def scenario():
        now, target, _, kube, repo, _, controller, _ = fixture(tmp_path)
        deployment, pods, replica_sets, node, hpas = inventory(target, now[0])
        termination = pods[0]['status']['containerStatuses'][0]['lastState']['terminated']
        termination[field] = value
        kube.observation = project(target, deployment, pods, replica_sets, node, hpas, now=now[0])

        assert (await controller.tick())['phase'] == 'observing'
        assert not repo.history

    asyncio.run(scenario())


@pytest.mark.parametrize('changes', [
    {'oom': False},
    {'fresh_oom': False},
    {'pod_uid': 'different-pod'},
    {'target': {'container': 'different-container'}},
    {'memory': AFTER},
])
def test_unverified_observation_leaves_incident_ranking_empty(tmp_path, changes):
    async def scenario():
        _, _, store, _, _, kwargs, _, prepare = verified_fixture(tmp_path)
        proposal = await prepare()
        observation = store.read(proposal['observation_ref'])
        if 'target' in changes:
            observation['target'].update(changes['target'])
        else:
            observation.update(changes)
        proposal['observation_ref'] = store.write('observations', observation)
        controller = MemoryController(**{**kwargs, 'incidents': Repository()})

        snapshot = await controller._incident(proposal, 'OPEN')
        assert snapshot['payload']['root_cause_ranking'] == []

    asyncio.run(scenario())


def test_explanation_selects_oom_candidate_without_fallback(tmp_path):
    async def scenario():
        _, _, _, _, repo, _, _, prepare = verified_fixture(tmp_path)
        proposal = await prepare()
        snapshot = await repo.get(proposal['incident_id'])
        narrative = (
            'Vấn đề: Container sentinelops-oom-demo bị dừng vì hết bộ nhớ.\n'
            'Bằng chứng: Kubernetes ghi nhận OOMKilled.\n'
            'Chưa chắc chắn: Cần xem số liệu bộ nhớ để biết vì sao bộ nhớ cạn.'
        )

        async def invoke(request):
            hypothesis, = request['root_cause_hypotheses']
            assert hypothesis['service'] == SERVICE
            card_id, = hypothesis['evidence_refs']
            assert any(card['card_id'] == card_id and 'OOMKilled' in card['fact']
                       for card in request['cards'])
            return {
                'incident_id': request['incident_id'],
                'revision': request['revision'],
                'prompt_version': request['prompt_version'],
                'card_ids': [card_id],
                'root_cause': {
                    'service': hypothesis['service'],
                    'score': hypothesis['score'],
                    'explanation': narrative,
                    'evidence_refs': [card_id],
                },
            }

        result = await explain_incident(
            envelope(snapshot, proposal['created_at']), provider=ExplanationProvider('test/oom-rca', invoke),
        )
        assert result['fallback'] is False
        assert result['provider'] == 'test/oom-rca'
        assert result['payload']['root_cause'] == {
            'service': SERVICE,
            'score': snapshot['payload']['root_cause_ranking'][0]['score'],
            'explanation': narrative,
            'evidence_refs': [EVIDENCE_ID],
        }

    asyncio.run(scenario())


def test_existing_unranked_incident_stays_unranked_on_later_revision(tmp_path):
    async def scenario():
        _, _, _, _, repo, _, controller, prepare = verified_fixture(tmp_path)
        proposal = await prepare()
        stored = repo.rows[proposal['incident_id']]
        stored['payload']['root_cause_ranking'] = []
        stored['context']['config_refs'] = ['config://pod-memory-limit-demo/v1']
        snapshot = await controller._incident(proposal, 'ESCALATED', state='escalated')
        assert snapshot['payload']['root_cause_ranking'] == []
        assert 'config://pod-memory-oom-rca/v1' not in snapshot['context']['config_refs']

    asyncio.run(scenario())
