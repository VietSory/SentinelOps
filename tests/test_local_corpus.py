from dataclasses import replace
import json
from pathlib import Path

import pytest

from sentinelops.experiments import local_corpus as corpus
from sentinelops.experiments.runner import ExperimentConfig
from sentinelops.telemetry.store import ArtifactStore


def test_registered_recipes_keep_four_roles_and_real_oracle_traffic():
    plan = corpus.load_plan()
    healthy = corpus.recipe(plan, 'train', 1, 'healthy')
    assert healthy.scenario == 'S0' and healthy.active_seconds == 6300 and healthy.all_entry_traffic
    pay = corpus.recipe(plan, 'train', 1, 'payment-gradual')
    inv = corpus.recipe(plan, 'train', 1, 'inventory-gradual')
    assert pay.scenario == inv.scenario == 'S3'
    assert pay.target_service == 'payment-service' and inv.target_service == 'inventory-service'
    assert len({healthy.seed, pay.seed, inv.seed}) == 3
    assert corpus.recipe(plan, 'calibration', 1, 'healthy').split == 'validation'
    assert corpus.recipe(plan, 'development-validation', 1, 'healthy').split == 'validation'
    assert corpus.group_name(plan, 'calibration', 1) != corpus.group_name(plan, 'development-validation', 1)


def test_sealed_registration_rejected_before_source_or_docker_reads(tmp_path, monkeypatch):
    plan = corpus.load_plan()
    monkeypatch.setattr(corpus, 'clean_source', lambda _: pytest.fail('sealed source read'))
    monkeypatch.setattr(corpus, 'docker', lambda *a: pytest.fail('sealed Docker read'))
    with pytest.raises(ValueError, match='sealed-final'):
        corpus.register_session(ArtifactStore(tmp_path), plan, role='sealed-final', ordinal=1, prepare=True)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('field,value', [('policy_sha256', '0'*64), ('sealed_final_capture_allowed', True),
    ('previous_capture_promotion_allowed', True), ('roles_in_order', ['train','test'])])
def test_forged_plan_rejected(tmp_path, field, value):
    plan = corpus.load_plan()
    plan[field] = value
    path = tmp_path / 'plan.json'; path.write_text(json.dumps(plan))
    with pytest.raises(ValueError): corpus.load_plan(path)


def test_prospective_binding_is_immutable_and_detects_changed_body(tmp_path):
    store = ArtifactStore(tmp_path)
    path = tmp_path / 'sessions/train-01/registration.json'
    value = {'proof': 'fixture-only'}
    ref = corpus.bind(store, path, value)
    assert corpus.read_binding(store, path) == (ref, value)
    with pytest.raises(FileExistsError): corpus.bind(store, path, {'proof': 'replacement'})
    path.write_text(json.dumps({'proof': 'corrupted'}))
    with pytest.raises(FileNotFoundError): corpus.read_binding(store, path)


@pytest.mark.parametrize('changes', [{'target_service':'checkout-service'}, {'all_entry_traffic':1},
    {'scenario':'S5','split':'validation','target_service':'inventory-service'}])
def test_new_capture_modes_do_not_change_frozen_s5_or_accept_bad_targets(changes):
    with pytest.raises(ValueError): replace(ExperimentConfig('S0','train'), **changes).validate()


def test_capture_roles_cannot_skip_or_return_to_an_earlier_partition(tmp_path):
    store = ArtifactStore(tmp_path)
    plan = corpus.load_plan()
    with pytest.raises(ValueError, match='insufficient registered'):
        corpus.require_role_order(store, plan, 'calibration')
    corpus.bind(store, tmp_path/'sessions/cal-01/registration.json', {'role':'calibration','group_id':'cal-01'})
    with pytest.raises(ValueError, match='earlier-role'):
        corpus.require_role_order(store, plan, 'train')


def test_new_group_cannot_stop_an_incomplete_source_session(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    corpus.bind(store, tmp_path/'sessions/train-01/registration.json', {'role':'train','group_id':'train-01'})
    monkeypatch.setattr(corpus, 'docker', lambda *a: pytest.fail('incomplete group reached Docker'))
    with pytest.raises(ValueError, match='finish the active'):
        corpus.close_completed_sessions(store, except_group='train-02')


def mock_registration_context(monkeypatch, *, old_clock=False):
    from datetime import datetime, timezone
    monkeypatch.setattr(corpus, 'clean_source', lambda _: 'a'*40)
    monkeypatch.setattr(corpus, 'configure_compose', lambda project, root, group: 'sentinelops-corpus-'+group)
    def snapshot(project, name):
        clock = '2000-01-01T00:00:00Z' if old_clock else datetime.now(timezone.utc).isoformat()
        return {'containers': {'fixture': {'created_at': clock, 'container_id':'fixture-id'}},
                'database_volume': {'name':'fixture-volume','created_at':clock}, 'images':{}}
    monkeypatch.setattr(corpus, 'context_snapshot', snapshot)
    monkeypatch.setattr(corpus, 'docker', lambda *a: '0' if a[:2] == ('compose','exec') else '')


def test_partial_provisioning_retries_own_intent_without_replacing_source(tmp_path, monkeypatch):
    import subprocess
    store = ArtifactStore(tmp_path)
    plan = corpus.load_plan()
    mock_registration_context(monkeypatch)
    calls = []
    def provision(*a, **k):
        calls.append(a)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, a[0])
    monkeypatch.setattr(corpus.subprocess, 'run', provision)
    with pytest.raises(subprocess.CalledProcessError):
        corpus.register_session(store, plan, role='train', ordinal=1, prepare=True)
    intent = corpus.read_binding(store, tmp_path/'contexts/train-01/provisioning.json')
    assert not (tmp_path/'sessions/train-01/registration.json').exists()
    ref, registered = corpus.register_session(store, plan, role='train', ordinal=1, prepare=True)
    assert corpus.read_binding(store, tmp_path/'contexts/train-01/provisioning.json') == intent
    assert registered['initial_payment_rows'] == 0 and registered['fitting_allowed_by_registration'] is False
    assert store.read(ref) == registered and len(calls) == 2


def test_old_source_creation_clock_cannot_be_registered_as_fresh(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    mock_registration_context(monkeypatch, old_clock=True)
    monkeypatch.setattr(corpus.subprocess, 'run', lambda *a, **k: None)
    with pytest.raises(ValueError, match='creation clocks'):
        corpus.register_session(store, corpus.load_plan(), role='train', ordinal=1, prepare=True)
    assert not (tmp_path/'sessions/train-01/registration.json').exists()


def test_docker_clock_conversion_preserves_explicit_timezone_semantics():
    assert corpus.docker_clock('2026-10-31T10:00:00+07:00') == '2026-10-31T03:00:00Z'
    with pytest.raises(ValueError, match='explicit timezone'):
        corpus.docker_clock('2026-10-31T10:00:00')


def test_end_context_rejects_restart_and_forged_or_missing_source_clock():
    from copy import deepcopy
    from sentinelops.contracts import utc_time
    session = {'compose_project':'sentinelops-corpus-train-01', 'registered_at':'2026-10-31T01:00:00Z',
               'context':{'containers':{'fixture':{'container_id':'id','image':'image','created_at':'2026-10-31T00:59:00Z'}}}}
    container = {'container_id':'id','image':'image','created_at':'2026-10-31T00:59:00Z',
        'started_at':'2026-10-31T00:59:30Z','finished_at':'0001-01-01T00:00:00Z','running':True,'restart_count':0}
    container.update({k+'_source':container[k] for k in ('created_at','started_at','finished_at')})
    observed = {'kind':'local-corpus-context-inspection/v1','compose_project':session['compose_project'],
                'containers':{'fixture':container},'collected_at':'2026-10-31T03:00:00Z'}
    end = utc_time('2026-10-31T02:00:00Z')
    corpus.validate_end_context(session, observed, end)
    changed = deepcopy(observed)
    changed['containers']['fixture'].update(started_at='2026-10-31T01:30:00Z', started_at_source='2026-10-31T01:30:00Z')
    with pytest.raises(ValueError, match='restarted'): corpus.validate_end_context(session,changed,end)
    changed = deepcopy(observed); changed['containers']['fixture']['started_at_source'] = '2026-10-31T00:59:31Z'
    with pytest.raises(ValueError, match='conversion'): corpus.validate_end_context(session,changed,end)
    changed = deepcopy(observed); changed['collected_at'] = '2026-10-31T01:00:00Z'
    with pytest.raises(ValueError, match='precedes'): corpus.validate_end_context(session,changed,end)


def test_context_inspection_cannot_open_sealed_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(corpus, 'clean_source', lambda _: pytest.fail('sealed source read'))
    with pytest.raises(ValueError, match='sealed'):
        corpus.inspect_completed_capture(ArtifactStore(tmp_path), 'sealed-final-01-healthy')


def test_acquisition_and_freeze_lock_is_exclusive_and_recovers_after_failure(tmp_path):
    from sentinelops.experiments.local_corpus import acquisition_lock
    store = ArtifactStore(tmp_path)
    with pytest.raises(RuntimeError):
        with acquisition_lock(store):
            with pytest.raises(ValueError,match='another acquisition/freeze'):
                with acquisition_lock(store): pytest.fail('concurrent acquisition was admitted')
            raise RuntimeError('simulated process failure')
    with acquisition_lock(store): pass
