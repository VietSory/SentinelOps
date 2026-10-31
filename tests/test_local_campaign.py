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
