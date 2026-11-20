"""One regression case for manual RCA preservation and the actual send boundary."""
import asyncio
from datetime import timedelta

from sentinelops.remediation import approvals
from sentinelops.remediation.pod_memory import MemoryController, OOM_EVIDENCE_ID, read_state
from test_pod_memory_rca import verified_fixture


def test_ranked_manual_recovery_presend_failure_and_unknown_patch_never_replay(tmp_path):
    async def scenario(failure):
        now, _, store, kube, repo, kwargs, controller, prepare = verified_fixture(tmp_path / failure)
        proposal = await prepare()
        original = await repo.get(proposal['incident_id'])
        ranking = original['payload']['root_cause_ranking']
        assert ranking and ranking[0]['evidence_refs'] == [OOM_EVIDENCE_ID]
        original_evidence = next(e for e in original['payload']['evidence'] if e['id'] == OOM_EVIDENCE_ID)
        approvals.record_approval(store, proposal_id=proposal['proposal_id'], approved_by='oncall',
            decision='approve', reason='Explicit bounded manual recovery', confirm_oncall_approval=True,
            now=now[0], approval_mode=approvals.MANUAL_MEMORY_RECOVERY)
        actual_write = repo.write
        async def write(command):
            # Real contract validation catches the original orphaned RCA ref.
            command.validate()
            if failure == 'before-send' and command.lifecycle_stage == 'RCA_READY':
                raise ValueError('simulated incident validation failure before PATCH')
            return await actual_write(command)
        repo.write = write
        kube.lose_reply = failure == 'lost-reply'
        state = await controller.tick()
        if failure == 'before-send':
            assert state['phase'] == 'blocked' and state['finalized'] and state['dispatches'] == 0
            assert approvals.read_slot(store, 'used', proposal['proposal_id']) is None
            assert kube.patches == []
            await MemoryController(**kwargs).tick()
            assert kube.patches == []
        else:
            assert len(kube.patches) == 1 and state['dispatches'] == 1
            assert state['phase'] == ('dispatching' if failure == 'lost-reply' else 'verifying')
            assert approvals.read_slot(store, 'used', proposal['proposal_id'])['approval_mode'] == approvals.MANUAL_MEMORY_RECOVERY
            for _ in range(16):
                now[0] += timedelta(seconds=2)
                state = await MemoryController(**kwargs).tick()
                if state.get('finalized'):
                    break
            durable = read_state(store, proposal['proposal_id'])
            assert durable['phase'] == 'recovered' and durable['finalized']
            assert durable['successful_probes'] >= 10 and durable['dispatches'] == 1
            assert len(kube.patches) == 1
        for snapshot in repo.history:
            assert snapshot['payload']['root_cause_ranking'] == ranking
            assert next(e for e in snapshot['payload']['evidence'] if e['id'] == OOM_EVIDENCE_ID) == original_evidence
            assert all(trigger in snapshot['context']['trigger_sources'] for trigger in original['context']['trigger_sources'])
            assert set(original['context']['source_refs']) <= set(snapshot['context']['source_refs'])
        latest = repo.history[-1]
        assert any(e['id'] == 'manual-memory-recovery' for e in latest['payload']['evidence'])
        assert any(t['source'] == 'manual' for t in latest['context']['trigger_sources'])
    async def all_paths():
        for failure in ('valid-ranked-manual', 'before-send', 'lost-reply'):
            await scenario(failure)
    asyncio.run(all_paths())
