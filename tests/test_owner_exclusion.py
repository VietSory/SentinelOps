"""Owner exclusion fixtures prove the lifecycle rules, never real source admission."""
import json

import pytest

from sentinelops.experiments import local_corpus as corpus
from sentinelops.experiments import local_recovery as recovery
from sentinelops.experiments import owner_exclusion as owner
from sentinelops.ml import local_data
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id
from sentinelops.telemetry.store import ArtifactStore

EPOCH, SUCCESSOR = 'a' * 40, 'b' * 40
DECISION = 'Owner 2026-11-11: host slept 7h48m inside alb-04; exclude the whole group once.'
ROLE = 'development-validation'


def write_ledger(store, slot, *, gap_seconds, bound=True):
    """A registered, completed capture whose immutable client ledger has one gap after the tenth request."""
    import shutil
    from datetime import datetime, timedelta, timezone
    root = store.root / 'runs' / slot
    shutil.rmtree(root, ignore_errors=True)
    run_dir = root / 'captures' / ('run-' + '1' * 32)
    run_dir.mkdir(parents=True)
    start = datetime(2026, 11, 11, 3, 55, tzinfo=timezone.utc)
    times = [start + timedelta(seconds=i + (gap_seconds if i >= 10 else 0)) for i in range(20)]
    stamp = lambda t: t.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    truth = ArtifactStore(run_dir / 'truth')
    ledger_ref = truth.write('raw', {'outcomes': [{'started_at': stamp(t), 'completed_at': stamp(t + timedelta(milliseconds=20))}
                                                  for t in times]})
    run = {'run_id': run_dir.name, 'status': 'completed', 'request_outcomes_ref': ledger_ref,
           'started_at': stamp(start - timedelta(seconds=1)), 'ended_at': stamp(times[-1] + timedelta(seconds=1))}
    (run_dir / 'run.json').write_text(json.dumps(run))
    if not bound:
        return
    truth.write('manifests', run)
    registration_ref = corpus.bind(store, root / 'registration.json', {'fixture_only': True, 'slot_id': slot})
    corpus.bind(store, root / 'result.json', {'status': 'completed', 'registration_ref': registration_ref,
                                              'run_sha256': stable_id(run),
                                              'run_directory': run_dir.relative_to(store.root).as_posix()})


@pytest.fixture
def epoch(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / 'corpus')
    plan = corpus.load_plan(corpus.ALIBABA_PLAN)
    names = list(BUSINESS_ROUTES) + ['postgres', 'prometheus', 'otel-collector', 'jaeger']
    containers = {name: {'container_id': f'{i + 1:064x}', 'image': 'sha256:' + 'f' * 64} for i, name in enumerate(names)}
    session = {'group_id': 'alb-04', 'role': ROLE, 'ordinal': 4, 'source_commit': EPOCH,
               'compose_project': 'sentinelops-corpus-alb-04', 'registered_at': '2026-11-11T02:10:00Z',
               'plan_sha256': stable_id(plan), 'context': {'containers': containers}}
    corpus.bind(store, corpus.registration_path(store, 'alb-04'), session)
    for kind in ('healthy', 'payment-abrupt'):
        root = store.root / 'runs' / ('alb-04-' + kind)
        root.mkdir(parents=True)
        corpus.bind(store, root / 'result.json', {'fixture_only': True, 'kind': kind})
        (root / 'raw.json').write_bytes(b'{"fixture_only":true}')
    write_ledger(store, 'alb-04-inventory-abrupt', gap_seconds=28094)
    state = {'failing': {'alb-04-inventory-abrupt'}, 'stopped': [], 'running': True}
    inspected = [{'Id': item['container_id'], 'Config': {'Labels': {'com.docker.compose.project': session['compose_project']}},
                  'State': {'Running': True}} for item in containers.values()]

    def docker(*args):
        if args[0] == 'stop':
            state['stopped'].extend(args[1:])
            state['running'] = False
            return ''
        for item in inspected:
            item['State']['Running'] = state['running']
        return json.dumps(inspected)

    def verify(store_, plan_, session_, kind, **_):
        if session_['group_id'] + '-' + kind in state['failing']:
            raise ValueError('lacks 30 consecutive verified healthy pre-fault minutes')
        return {}

    monkeypatch.setattr(owner, 'docker', docker)
    monkeypatch.setattr(owner, 'clean_source', lambda *_: SUCCESSOR)
    monkeypatch.setattr(owner, '_descends', lambda project, ancestor, commit: ancestor == EPOCH and commit != 'c' * 40)
    monkeypatch.setattr(owner, 'read_alibaba_epoch', lambda *_: ('epoch-ref', {'source_commit': EPOCH}))
    monkeypatch.setattr(local_data, 'verify_capture', verify)
    monkeypatch.setattr(local_data, '_session_identity', lambda *_: None)
    return store, plan, session, state


def exclude(fixture, **overrides):
    store, plan, _, _ = fixture
    return owner.exclude_group(store, plan, **{'role': ROLE, 'ordinal': 4, 'replacement_ordinal': 6,
                                               'decision': DECISION, **overrides})


def test_owner_exclusion_keeps_every_byte_stops_the_group_and_names_the_successor(epoch):
    store, plan, session, state = epoch
    before = recovery._inventory(store, 'alb-04', plan)
    result = exclude(epoch)
    assert result['replacement_group_id'] == 'alb-06' and result['successor_source_commit'] == SUCCESSOR
    assert result['slot_status'] == {'alb-04-healthy': 'SOURCE_VERIFIED', 'alb-04-payment-abrupt': 'SOURCE_VERIFIED',
                                     'alb-04-inventory-abrupt': 'FAILED_VERIFICATION',
                                     'alb-04-payment-gradual': 'INCOMPLETE', 'alb-04-inventory-gradual': 'INCOMPLETE'}
    assert sorted(state['stopped']) == sorted(c['container_id'] for c in session['context']['containers'].values())
    assert recovery._inventory(store, 'alb-04', plan) == before
    ref, closed = recovery.read_abandonment(store, session, plan)
    assert closed['closure_version'] == owner.VERSION and closed['owner_decision'] == DECISION
    assert owner.epoch_sources(store, plan) == {EPOCH, SUCCESSOR}


def test_a_fully_verified_group_cannot_be_excluded(epoch):
    store, plan, _, state = epoch
    state['failing'].clear()
    for kind in ('payment-gradual', 'inventory-gradual'):
        root = store.root / 'runs' / ('alb-04-' + kind)
        root.mkdir(parents=True)
        corpus.bind(store, root / 'result.json', {'fixture_only': True})
    with pytest.raises(ValueError, match='fully verified'):
        exclude(epoch)
    assert not (store.root / 'sessions/alb-04/closed.json').exists() and state['stopped'] == []


@pytest.mark.parametrize(('overrides', 'source', 'message'), [
    ({'decision': 'too short'}, SUCCESSOR, 'written owner decision'),
    ({'replacement_ordinal': 5}, SUCCESSOR, 'outside the reserved minimum'),
    ({'replacement_ordinal': 4}, SUCCESSOR, 'outside the reserved minimum'),
    ({}, EPOCH, 'descendant of the epoch source'),
    ({}, 'c' * 40, 'descendant of the epoch source'),
])
def test_exclusion_needs_a_decision_a_spare_ordinal_and_a_descendant_source(epoch, monkeypatch, overrides, source, message):
    store = epoch[0]
    monkeypatch.setattr(owner, 'clean_source', lambda *_: source)
    with pytest.raises(ValueError, match=message):
        exclude(epoch, **overrides)
    assert not (store.root / 'sessions/alb-04/closed.json').exists()


def test_source_changed_after_the_decision_is_detected(epoch):
    store, plan, session, _ = epoch
    exclude(epoch)
    (store.root / 'runs/alb-04-healthy/raw.json').write_bytes(b'{"edited":true}')
    with pytest.raises(ValueError, match='changed after the owner decision'):
        recovery.read_abandonment(store, session, plan)


def test_slot_status_must_replay_exactly(epoch):
    store, plan, session, state = epoch
    exclude(epoch)
    state['failing'].clear()  # a later verifier cannot silently turn the excluded slot into good data
    with pytest.raises(ValueError, match='slot status differs'):
        recovery.read_abandonment(store, session, plan)


def test_closure_fields_are_exact(epoch):
    store, plan, session, _ = epoch
    exclude(epoch)
    path = store.root / 'sessions/alb-04/closed.json'
    ref, closed = corpus.read_binding(store, path)
    for change in ({'replacement_group_id': 'alb-07'}, {'reason': 'other'}, {'extra': True}):
        with pytest.raises(ValueError, match='differs from the frozen'):
            owner.read_owner_exclusion(store, session, plan, {**closed, **change}, ref)


def test_only_one_owner_exclusion_per_epoch(epoch):
    store, plan, session, _ = epoch
    exclude(epoch)
    other = {**session, 'group_id': 'alb-05', 'ordinal': 5, 'compose_project': 'sentinelops-corpus-alb-05'}
    corpus.bind(store, corpus.registration_path(store, 'alb-05'), other)
    with pytest.raises(ValueError):
        owner.exclude_group(store, plan, role=ROLE, ordinal=5, replacement_ordinal=7, decision=DECISION)


def test_a_failure_without_a_host_pause_witness_cannot_be_excluded(tmp_path, monkeypatch, epoch):
    store, plan, _, state = epoch
    write_ledger(store, 'alb-04-inventory-abrupt', gap_seconds=0)  # same run directory, regular ledger
    with pytest.raises(ValueError, match='host-pause witness'):
        exclude(epoch)
    assert not (store.root / 'sessions/alb-04/closed.json').exists() and state['stopped'] == []


def test_the_witness_is_recorded_and_replayed_from_the_original_ledger(epoch):
    store, plan, session, _ = epoch
    result = exclude(epoch)
    witness = result['host_pause_witness']['alb-04-inventory-abrupt']
    assert witness['gaps'][0]['seconds'] > owner.PAUSE_SECONDS and witness['attempts'] == 20
    path = store.root / 'sessions/alb-04/closed.json'
    ref, closed = corpus.read_binding(store, path)
    forged = {**closed, 'host_pause_witness': {'alb-04-payment-gradual': witness}}
    with pytest.raises(ValueError, match='witness differs'):
        owner.read_owner_exclusion(store, session, plan, forged, ref)


def test_the_operator_command_requires_the_written_decision(monkeypatch, tmp_path, capsys):
    import runpy
    import sys
    calls = []
    monkeypatch.setattr(owner, 'exclude_group', lambda *args, **kwargs: calls.append(kwargs) or {'ok': True})
    script = str(owner.PROJECT / 'scripts/exclude_incident_group.py')
    monkeypatch.setattr(sys, 'argv', [script, '--root', str(tmp_path), '--group', '4', '--replacement-group', '6'])
    with pytest.raises(SystemExit):
        runpy.run_path(script, run_name='__main__')
    assert calls == []
    monkeypatch.setattr(sys, 'argv', [script, '--root', str(tmp_path), '--group', '4', '--replacement-group', '6',
                                      '--decision', DECISION])
    runpy.run_path(script, run_name='__main__')
    assert calls == [{'role': ROLE, 'ordinal': 4, 'replacement_ordinal': 6, 'decision': DECISION}]


def test_the_schedule_follows_the_exclusion_to_its_replacement(epoch):
    from sentinelops.experiments.local_campaign import planned_slots
    store, plan, _, _ = epoch
    exclude(epoch)
    groups = {f'alb-0{i}' for i in (1, 2, 3, 5)}
    slots = recovery.effective_slots(store, plan, planned_slots(plan))
    assert {f'alb-0{ordinal}' for _, ordinal, _ in slots} == groups | {'alb-06'}


def test_loose_files_in_an_incomplete_slot_are_never_a_witness(epoch):
    store, plan, _, state = epoch
    write_ledger(store, 'alb-04-inventory-abrupt', gap_seconds=0)
    write_ledger(store, 'alb-04-payment-gradual', gap_seconds=28094, bound=False)  # forged, unregistered
    with pytest.raises(ValueError, match='host-pause witness'):
        exclude(epoch)
    assert state['stopped'] == []


def test_a_ledger_that_is_not_the_bound_capture_is_refused(epoch):
    store, plan, _, state = epoch
    run_json = next((store.root / 'runs/alb-04-inventory-abrupt/captures').glob('run-*/run.json'))
    run = json.loads(run_json.read_text())
    run_json.write_text(json.dumps({**run, 'ended_at': '2030-01-01T00:00:00.000000Z'}))
    with pytest.raises(ValueError, match='registered completed capture'):
        exclude(epoch)
    assert state['stopped'] == []
