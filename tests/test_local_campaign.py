from datetime import datetime, timedelta, timezone
import pytest
from sentinelops.experiments.local_campaign import (planned_slots, campaign_lock,
    await_existing_result, source_checkout)
from sentinelops.experiments.local_corpus import load_plan, recipe, bind
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.telemetry.core import timestamp


def test_campaign_has_exact_bounded_nonfinal_roles_groups_and_kinds():
    slots = planned_slots(load_plan())
    assert len(slots) == 30 and len(set(slots)) == 30
    assert [len({ordinal for role,ordinal,kind in slots if role == target})
            for target in ('train','calibration','development-validation')] == [3,2,5]
    assert all(role != 'sealed-final' for role,_,_ in slots)
    assert [r for r,_,_ in slots] == sorted([r for r,_,_ in slots],
        key=('train','calibration','development-validation').index)


def test_campaign_lock_rejects_concurrent_controllers(tmp_path):
    store = ArtifactStore(tmp_path)
    with campaign_lock(store):
        with pytest.raises(ValueError,match='another campaign'):
            with campaign_lock(store): pytest.fail('concurrent controller')
    with campaign_lock(store): pass


def test_waiting_for_an_existing_capture_never_replays_or_overwrites(tmp_path):
    store = ArtifactStore(tmp_path)
    start = datetime(2026,10,31,tzinfo=timezone.utc)
    slot = 'train-01-healthy'
    root = tmp_path/'runs'/slot
    bind(store,root/'registration.json',{'registered_at':timestamp(start)})
    with pytest.raises(ValueError,match='incomplete beyond its budget'):
        await_existing_result(store,slot,recipe(load_plan(),'train',1,'healthy'),
            now=lambda:start+timedelta(days=1),sleep=lambda _:pytest.fail('expired capture slept'))
    assert not (root/'result.json').exists()
    expected = bind(store,root/'result.json',{'status':'completed','fixture_only':True})
    assert await_existing_result(store,slot,recipe(load_plan(),'train',1,'healthy'))[0] == expected


def test_failed_existing_capture_is_retained_without_replacement(tmp_path):
    import json
    store = ArtifactStore(tmp_path)
    root = tmp_path/'runs/train-01-healthy'
    bind(store,root/'registration.json',{'registered_at':'2026-10-31T00:00:00Z'})
    capture = root/'captures/run-fixture'
    capture.mkdir(parents=True)
    (capture/'run.json').write_text(json.dumps({'status':'failed'}))
    with pytest.raises(ValueError,match='existing capture failed'):
        await_existing_result(store,'train-01-healthy',recipe(load_plan(),'train',1,'healthy'))
    assert (capture/'run.json').exists() and not (root/'result.json').exists()


def test_registered_source_checkout_requires_exact_clean_commit(tmp_path,monkeypatch):
    from sentinelops.experiments import local_campaign as campaign
    monkeypatch.setattr(campaign.subprocess,'check_output',lambda *a,**k:
        'worktree '+str(tmp_path)+'\nHEAD '+'a'*40+'\ndetached\n\n')
    monkeypatch.setattr(campaign,'clean_source',lambda _: 'a'*40)
    assert source_checkout(tmp_path,'a'*40) == tmp_path
    with pytest.raises(ValueError,match='original clean source'):
        source_checkout(tmp_path,'b'*40)


def test_later_partition_cannot_begin_before_measured_prior_role_readiness():
    from sentinelops.experiments.local_campaign import require_completed_role_admission
    report = {'failed_conditions':[{'condition':'train/payment-service/healthy_windows','available':299,'required':300},
                                   {'condition':'calibration/payment-service/healthy_windows','available':0,'required':100}],
              'quarantined_slots':[]}
    with pytest.raises(ValueError,match='previous role lacks source-admitted support'):
        require_completed_role_admission(report,'train')
    report['failed_conditions'] = report['failed_conditions'][1:]
    require_completed_role_admission(report,'train')
    report['quarantined_slots'] = [{'slot':'train-01-healthy','reason':'corrupted source'}]
    with pytest.raises(ValueError,match='quarantine'):
        require_completed_role_admission(report,'train')


def test_controller_successor_preserves_original_registration_and_frozen_plan(tmp_path,monkeypatch):
    from copy import deepcopy
    from sentinelops.experiments import local_campaign as campaign
    store = ArtifactStore(tmp_path)
    original = {'campaign_version':'local-http-acquisition/v1','controller_source_commit':'a'*40,
                'plan_sha256':'b'*64,'slots':[['train',1,'healthy']], 'sealed_final_allowed':False,
                'automatic_fitting_allowed':False}
    reference = campaign.register_controller(store,tmp_path,original)
    saved = (tmp_path/'campaign/registration.json').read_bytes()
    calls = []
    monkeypatch.setattr(campaign.subprocess,'run',lambda args,**kwargs:calls.append(args))
    successor = {**original,'controller_source_commit':'c'*40}
    updated = campaign.register_controller(store,tmp_path,successor)
    assert updated != reference
    assert campaign.register_controller(store,tmp_path,successor) == updated
    assert (tmp_path/'campaign/registration.json').read_bytes() == saved
    assert calls[0] == ['git','merge-base','--is-ancestor','a'*40,'c'*40]
    changed = deepcopy(successor); changed['slots'].append(['sealed-final',1,'healthy'])
    with pytest.raises(ValueError,match='plan/roles/budgets are frozen'):
        campaign.register_controller(store,tmp_path,changed)


def test_retry_after_a_failed_provisioning_keeps_the_earlier_log(tmp_path):
    from sentinelops.experiments.local_campaign import attempt_log
    from sentinelops.telemetry.store import ArtifactStore
    store = ArtifactStore(tmp_path)
    first = attempt_log(store, 'det-04-healthy')
    first.write_text('failed attempt')
    second = attempt_log(store, 'det-04-healthy')
    assert first.name == 'det-04-healthy.log' and second.name == 'det-04-healthy.attempt-2.log'
    assert first.read_text() == 'failed attempt'


def test_window_end_pauses_only_at_a_group_boundary_without_starting_capture(tmp_path, monkeypatch):
    from sentinelops.experiments import local_campaign as campaign
    from sentinelops.experiments.local_corpus import ALIBABA_PLAN
    plan = load_plan(ALIBABA_PLAN)
    store = ArtifactStore(tmp_path)
    monkeypatch.setattr(campaign, 'clean_source', lambda _: 'a' * 40)
    monkeypatch.setattr(campaign, 'register_alibaba_epoch', lambda *a, **k: 'artifact://manifests/' + 'e' * 64 + '.json')
    monkeypatch.setattr(campaign, 'register_controller', lambda *a, **k: 'ref')
    started = []

    class Started(Exception):
        pass

    def run(args, **kwargs):
        started.append(args)
        raise Started()
    monkeypatch.setattr(campaign.subprocess, 'run', run)
    now = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)  # 13:00 at UTC+7
    needed = campaign.group_budget_seconds(plan, 'development-validation', 1)
    assert 4 * 3600 < needed < 5 * 3600
    late = campaign.run_campaign(store, plan_path=ALIBABA_PLAN, bundle='b', manifest_sha256='m',
                                 window_end=now + timedelta(seconds=needed - 1), clock=lambda: now)
    assert late['status'] == 'PAUSED_AT_GROUP_BOUNDARY' and late['next_group'] == 'alc-01' and started == []
    with pytest.raises(Started):  # enough time: the first slot of the group starts
        campaign.run_campaign(store, plan_path=ALIBABA_PLAN, bundle='b', manifest_sha256='m',
                              window_end=now + timedelta(seconds=needed), clock=lambda: now)
    assert len(started) == 1


@pytest.mark.parametrize("stop_ok", [True, False])
def test_pause_closes_verified_group_before_reporting_boundary(tmp_path, monkeypatch, stop_ok):
    from sentinelops.experiments import local_campaign as campaign
    from sentinelops.experiments import local_corpus as corpus

    plan = load_plan(corpus.ALIBABA_PLAN)
    store = ArtifactStore(tmp_path)
    group = 'alc-01'
    project = 'sentinelops-corpus-alc-01'
    container = 'a' * 64
    kinds = corpus.plan_kinds(plan)
    bind(store, corpus.registration_path(store, group), {
        'group_id': group, 'compose_project': project, 'role': 'development-validation', 'ordinal': 1,
        'context': {'containers': {'postgres': {'container_id': container}}},
    })
    for kind in kinds:
        bind(store, tmp_path / 'runs' / (group + '-' + kind) / 'result.json',
             {'status': 'completed'})

    monkeypatch.setattr(campaign, 'clean_source', lambda _: 'a' * 40)
    monkeypatch.setattr(campaign, 'register_alibaba_epoch', lambda *a, **k: 'epoch')
    monkeypatch.setattr(campaign, 'register_controller', lambda *a, **k: 'controller')
    monkeypatch.setattr(campaign, '_audit', lambda *a: ({'status': 'BLOCKED', 'verified_completed_runs': 5}, [], []))
    monkeypatch.setattr(campaign, 'verify_capture', lambda *a, **k: {
        'source_result_ref': 'result', 'oracle_ref': 'oracle', 'rows': [],
    })
    verified = []
    monkeypatch.setattr(campaign, 'inspect_completed_capture',
                        lambda store, slot, **kwargs: verified.append(slot))
    monkeypatch.setattr(corpus, 'docker', lambda *args:
        '[{"Config":{"Labels":{"com.docker.compose.project":"sentinelops-corpus-alc-01"}}}]')

    def stop(args, **kwargs):
        assert args == ['docker', 'stop', container]
        assert verified == [group + '-' + kind for kind in kinds]
        if not stop_ok:
            raise RuntimeError("stop failed")

    monkeypatch.setattr(corpus.subprocess, 'run', stop)
    now = datetime(2026, 11, 10, 7, tzinfo=timezone.utc)
    options = dict(plan_path=corpus.ALIBABA_PLAN, bundle='b', manifest_sha256='m',
                   window_end=now + timedelta(hours=4), clock=lambda: now)
    if stop_ok:
        result = campaign.run_campaign(store, **options)
        assert result['status'] == 'PAUSED_AT_GROUP_BOUNDARY'
        assert result['next_group'] == 'alc-02'
    else:
        with pytest.raises(RuntimeError, match='stop failed'):
            campaign.run_campaign(store, **options)
    assert (tmp_path / 'sessions' / group / 'closed.json').exists() == stop_ok
    events = sorted((tmp_path / 'campaign' / 'events').glob('*.json'))
    _, latest = corpus.read_binding(store, events[-1])
    assert latest['status'] == ('PAUSED_AT_GROUP_BOUNDARY' if stop_ok else 'STOPPED_REQUIRES_INVESTIGATION')
    assert not corpus.registration_path(store, 'alc-02').exists()


def test_alibaba_plan_keeps_five_acquired_groups_with_a_replacement_budget():
    from sentinelops.experiments.local_corpus import ALIBABA_PLAN, group_name
    plan = load_plan(ALIBABA_PLAN)
    assert {ordinal for _, ordinal, _ in planned_slots(plan)} == {1, 2, 3, 4, 5}
    assert group_name(plan, 'development-validation', 6) == 'alc-06'  # an interrupted group can be replaced
