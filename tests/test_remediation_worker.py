"""Software fixtures for resume-only behavior, never production H4 evidence."""
import asyncio
from dataclasses import asdict, replace
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from sentinelops.remediation.domain import ActionConflict, KubernetesUnavailable
from sentinelops.remediation.executor import RollbackExecutor
from sentinelops.remediation.worker import ReconciliationWorker
from test_remediation_executor import setup
from test_rca import POLICY


def resume(f):
    async def ready(): pass
    async def active(target): return await f.journal.get(None)
    f.journal.check_ready = ready
    f.journal.active = active
    # Fresh process, disabled, with neither authority nor known-good provider.
    executor = RollbackExecutor(f.journal,f.kube,None,None,f.recovery,
        replace(f.executor.policy,enabled=False),POLICY,clock=f.clock)
    return ReconciliationWorker(f.journal,executor)


def test_restart_observes_uncertain_sent_patch_without_resend_then_verifies():
    async def run():
        f=setup();f.kube.timeout=True
        state=await f.executor.execute(f.request)
        assert state['phase']=='dispatching'
        worker=resume(f)
        assert (await worker.tick())['phase']=='verifying'
        f.time[0]+=timedelta(minutes=4)
        assert (await worker.tick())['phase']=='recovered'
        assert f.kube.calls==[True,False]
    asyncio.run(run())


def test_armed_but_unsent_crash_never_dispatches_and_eventually_quarantines():
    async def run():
        f=setup()
        prepared=await f.executor._prepare(f.request)
        state=await f.journal.propose(f.request,f.executor.policy,evidence=prepared[3])
        await f.journal.arm(state['action_id'],state['revision'])
        worker=resume(f)
        assert (await worker.tick())['phase']=='dispatching'
        f.time[0]+=timedelta(seconds=601)
        assert (await worker.tick())['status']=='QUARANTINED'
        assert (await worker.tick())['status']=='QUARANTINED'
        assert f.kube.calls==[True]
    asyncio.run(run())


def test_prepared_intent_is_canceled_not_dispatched_or_given_new_authority():
    async def run():
        f=setup()
        await f.journal.propose(f.request,f.executor.policy,evidence={})
        worker=resume(f)
        assert (await worker.tick())['phase']=='blocked'
        assert f.journal.state['facts']['reason']=='prepared-intent-requires-explicit-revalidation'
        assert f.kube.calls==[]
    asyncio.run(run())


def test_corrupt_binding_stops_without_kubernetes_or_clearing_claim():
    async def run():
        f=setup();await f.journal.propose(f.request,f.executor.policy,evidence={})
        f.journal.state['intent']['policy']['target']['deployment_uid']='wrong'
        with pytest.raises(RuntimeError,match='different target'): await resume(f).tick()
        assert f.journal.state['phase']=='prepared' and f.kube.calls==[]
    asyncio.run(run())


def test_retry_budget_backoff_recovers_and_logs_no_raw_dependency_details(caplog):
    async def run():
        f=setup();worker=resume(f);calls=[]
        async def tick():
            calls.append(1)
            if len(calls)==1: raise ConnectionError('postgresql://SECRET@private/db')
            return {'status':'IDLE'}
        worker.tick=tick
        assert (await worker.run(asyncio.Event(),poll_seconds=.25,max_ticks=2))['ticks']==2
        assert len(calls)==2
        assert 'SECRET' not in caplog.text and 'ConnectionError' in caplog.text
        with pytest.raises(RuntimeError,match='retry budget exhausted'):
            async def unavailable(): raise KubernetesUnavailable('SECRET')
            worker.tick=unavailable
            await worker.run(asyncio.Event(),max_failures=1)
    asyncio.run(run())


def test_fatal_integrity_errors_are_not_retried_and_shutdown_is_interruptible():
    async def run():
        f=setup();worker=resume(f)
        async def corrupt(): raise RuntimeError('audit invalid')
        worker.tick=corrupt
        with pytest.raises(RuntimeError,match='audit invalid'):
            await worker.run(asyncio.Event(),max_ticks=3)
        stop=asyncio.Event()
        async def stop_tick(): stop.set();return {'status':'IDLE'}
        worker.tick=stop_tick
        assert await asyncio.wait_for(worker.run(stop,poll_seconds=10),timeout=1)=={'status':'STOPPED','ticks':1}
    asyncio.run(run())


def test_worker_timeout_does_not_consume_an_attempt_or_hide_failure():
    async def run():
        f=setup();worker=resume(f);worker.timeout_seconds=.01
        async def slow(): await asyncio.sleep(10)
        f.journal.check_ready=slow
        with pytest.raises(TimeoutError): await worker.tick()
        assert f.journal.state is None and f.kube.calls==[]
    asyncio.run(run())


def test_worker_cannot_be_constructed_with_enabled_dispatch_policy():
    f=setup()
    with pytest.raises(ValueError,match='disabled'): ReconciliationWorker(f.journal,f.executor)


@pytest.mark.parametrize('dsn',[
    '', 'postgresql://writer:SECRET@remote.example:5432/sentinelops_control',
    'postgresql://writer:SECRET@127.0.0.1/sentinelops_control',
    'postgresql://writer:SECRET@127.0.0.1:5432/commerce',
    'postgresql://writer:SECRET@127.0.0.1:5432/sentinelops_control?sslmode=disable',
    'postgresql://writer@127.0.0.1:5432/sentinelops_control'])
def test_runner_rejects_ambient_remote_or_incomplete_database_configuration(tmp_path,dsn):
    from scripts.run_remediation_worker import validate_configuration
    f=setup();target=tmp_path/'target.json';target.write_text(json.dumps(asdict(f.executor.policy.target)))
    args=SimpleNamespace(health_stream='a'*64,source_store=tmp_path,target=target,server='https://127.0.0.1:6443')
    with pytest.raises(ValueError,match='explicit loopback'): validate_configuration(args,dsn)


def test_runner_requires_selected_original_source_and_exact_target(tmp_path):
    from scripts.run_remediation_worker import validate_configuration
    f=setup();target=tmp_path/'target.json';target.write_text(json.dumps(asdict(f.executor.policy.target)))
    args=SimpleNamespace(health_stream='a'*64,source_store=tmp_path,target=target,server='https://127.0.0.1:6443')
    dsn='postgresql://writer:fixture@127.0.0.1:15433/sentinelops_control'
    assert validate_configuration(args,dsn)==f.executor.policy.target
    args.source_store=tmp_path/'missing'
    with pytest.raises(ValueError,match='already exist'): validate_configuration(args,dsn)
