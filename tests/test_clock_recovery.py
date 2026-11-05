"""Lifecycle fixtures mock source execution proof; they never admit real data."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from test_local_recovery import interrupted
from sentinelops.experiments import clock_recovery as clock
from sentinelops.experiments import local_corpus as corpus
from sentinelops.experiments import local_recovery as recovery
from sentinelops.experiments.local_campaign import campaign_lock, planned_slots
from sentinelops.ml.local_data import audit_local_corpus
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore

ROOT = Path(__file__).resolve().parents[1]


def attempts(negative=True):
    return [{'request_entry_service': 'inventory-service', 'trace_id': '1'*32, 'correlation_id': 'fixture',
        'started_at': '2020-01-01T00:10:46.511476Z',
        'completed_at': '2020-01-01T00:10:45.927378Z' if negative else '2020-01-01T00:10:46.977000Z',
        'client_duration_ms': 465.275, 'status': 200}]


def build_slot(store, plan, session, kind, *, negative=False, partial=False):
    root = store.root/'runs'/(session['group_id']+'-'+kind)
    root.mkdir(parents=True, exist_ok=True)
    registered = {'capture_registration_version': 'local-http-capture/v1',
        'session_ref': 'artifact://manifests/'+stable_id(session)+'.json',
        'slot_id': root.name, 'source_commit': session['source_commit'], 'policy_sha256': plan['policy_sha256'],
        'recipe': asdict(corpus.recipe(plan, session['role'], session['ordinal'], kind)),
        'registered_at': '2020-01-01T00:01:30Z'}
    pointer = root/'registration.json'; pointer.unlink(missing_ok=True)
    registration_ref = corpus.bind(store, pointer, registered)
    run = json.loads((ROOT/'samples/experiment-run.sample.json').read_bytes())
    identifier = 'run-'+stable_id([session['group_id'],kind])[:32]
    captured = root/'captures'/identifier
    truth = ArtifactStore(captured/'truth')
    run.update(run_id=identifier, session_id=session['group_id'], source_commit=session['source_commit'],
        source_profile=plan['source_profile'], image_digests=session['context']['images'],
        started_at='2020-01-01T00:02:00Z', ended_at='2020-01-01T00:20:00Z',
        status='failed' if partial else 'completed', failure_reason='ClientClockDiscontinuity' if partial else None,
        scenario_id=registered['recipe']['scenario'], declared_split=registered['recipe']['split'],
        seed=registered['recipe']['seed'],
        root_cause_ground_truth=None if kind=='healthy' else registered['recipe']['target_service'],
        affected_services_ground_truth=[] if kind=='healthy' else [registered['recipe']['target_service'],'checkout-service'],
        fault_method='none' if kind=='healthy' else 'gradual-latency',
        fault_start_at=None if kind=='healthy' else '2020-01-01T00:07:00Z',
        recovery_start_at=None if kind=='healthy' else '2020-01-01T00:16:00Z',
        warmup_end_at='2020-01-01T00:07:00Z', phase_intervals=[],
        parameters={**registered['recipe'], 'slo':asdict(clock.SLOConfig()),
            'loaded_source_sha256':{'/frozen/src/sentinelops/experiments/client_clock.py':'e'*64}},
        request_outcomes_ref=truth.write('raw', {'outcomes':attempts(negative)}))
    truth.write('manifests',run); (captured/'run.json').write_text(json.dumps(run))
    result = root/'result.json'; result.unlink(missing_ok=True)
    if not partial:
        corpus.bind(store,result,{'status':'completed','registration_ref':registration_ref,
            'run_directory':captured.relative_to(store.root).as_posix(),'run_sha256':stable_id(run),
            'completed_at':'2020-01-01T00:20:30Z'})
    return captured, run


@pytest.fixture
def invalid_clock(interrupted, monkeypatch):
    store, plan, session, actual = interrupted
    for kind in corpus.KINDS:
        build_slot(store,plan,session,kind,negative=kind=='inventory-gradual')
    monkeypatch.setattr(clock,'clean_source',lambda *_:'d'*40)
    monkeypatch.setattr(clock,'_code_hash',lambda *_:'e'*64)
    monkeypatch.setattr(clock,'docker',lambda *_:json.dumps(actual))
    monkeypatch.setattr(clock,'_source_files',lambda *_:None)
    calls=[]
    def verify(*args,**kwargs):
        kind=args[3];calls.append(kind)
        if kind=='inventory-gradual':raise ValueError(clock.CLOCK_ERROR)
        return {}
    monkeypatch.setattr(clock,'verify_capture',verify)
    return store,plan,session,actual,calls


def close(fixture,**changes):
    store,plan,*_=fixture
    return clock.abandon_clock_invalid_group(store,plan,
        **{'role':'train','ordinal':1,'replacement_ordinal':4,**changes})


def test_original_negative_pair_produces_exact_witness_and_positive_duration_never_repairs_it():
    rows=attempts();original=deepcopy(rows)
    result=clock.client_clock_witness(rows,'2020-01-01T00:02:00Z','2020-01-01T00:20:00Z')
    assert result['invalid_count']==1 and result['samples'][0]['index']==0
    assert result['samples'][0]['client_duration_ms']==465.275 and rows==original
    assert clock.client_clock_witness(attempts(False),'2020-01-01T00:02:00Z','2020-01-01T00:20:00Z')['invalid_count']==0


def test_explicit_disposition_excludes_every_original_kind_and_keeps_original_bytes(invalid_clock):
    store,plan,session,_,calls=invalid_clock
    before=recovery._inventory(store,session['group_id'])
    result=close(invalid_clock)
    assert recovery._inventory(store,session['group_id'])==before
    assert result['source_preserved'] and result['excluded_whole_group'] and not result['activation_allowed']
    assert result['replacement_group_id']=='train-04'
    ref,value=recovery.read_abandonment(store,session,plan)
    assert ref==result['closure_ref'] and value['source_proof'][-1]['witness']['invalid_count']==1
    assert all(kind in calls for kind in corpus.KINDS)
    report,rows,cases=audit_local_corpus(store,plan=plan)
    assert not rows and not cases and not report['training_ready']
    assert len(report['excluded_groups'])==1 and report['verified_completed_runs']==0
    assert report['pending_slots']==[] and report['quarantined_slots']==[]
    assert all(v==0 for services in report['counts'].values() for counts in services.values() for v in counts.values())
    assert recovery.effective_slots(store,plan,planned_slots(plan))[:3]==[('train',4,k) for k in corpus.KINDS]


def test_partial_dedicated_guard_failure_requires_original_negative_attempt_and_provenance(invalid_clock):
    store,plan,session,_,_=invalid_clock
    build_slot(store,plan,session,'inventory-gradual',negative=True,partial=True)
    value=close(invalid_clock)
    proof=value['source_proof'][-1]
    assert proof['status']=='CLOCK_INVALID' and 'source_result_ref' not in proof
    assert proof['witness']['invalid_count']==1


@pytest.mark.parametrize('failure',['no-negative','generic-failure','unrelated-client','unexpected-source','no-guard-proof'])
def test_flags_or_generic_corruption_never_authorize_clock_disposition(invalid_clock,failure,monkeypatch):
    store,plan,session,_,_=invalid_clock
    path,run=build_slot(store,plan,session,'inventory-gradual',negative=failure!='no-negative',partial=True)
    if failure=='generic-failure':run['failure_reason']='HTTPStatusError'
    elif failure=='unrelated-client':
        run['request_outcomes_ref']=ArtifactStore(path/'truth').write('raw',{'outcomes':[{**attempts()[0],'status':True}]})
    elif failure=='unexpected-source':
        build_slot(store,plan,session,'inventory-gradual',negative=True)
        monkeypatch.setattr(clock,'verify_capture',lambda *_a,**_k:(_ for _ in ()).throw(ValueError('unrelated raw source failure')))
    elif failure=='no-guard-proof':run['parameters']['loaded_source_sha256']={}
    if failure!='unexpected-source':
        ArtifactStore(path/'truth').write('manifests',run);(path/'run.json').write_text(json.dumps(run))
    with pytest.raises(ValueError):close(invalid_clock)
    assert not (store.root/'sessions/train-01/closed.json').exists()


@pytest.mark.parametrize('ordinal',[True,1,2,3,11])
def test_replacement_keeps_reserved_groups_and_maximum(invalid_clock,ordinal):
    with pytest.raises(ValueError):close(invalid_clock,replacement_ordinal=ordinal)


def test_active_locks_frozen_final_later_role_and_used_replacement_are_refused(invalid_clock):
    store,_,_,_,_=invalid_clock
    with campaign_lock(store):
        with pytest.raises(ValueError,match='another campaign'):close(invalid_clock)
    with corpus.acquisition_lock(store):
        with pytest.raises(ValueError,match='another acquisition'):close(invalid_clock)
    with pytest.raises(ValueError,match='sealed-final'):close(invalid_clock,role='sealed-final')
    (store.root/'dataset-freeze.json').write_text('{}')
    with pytest.raises(ValueError,match='frozen'):close(invalid_clock)
    (store.root/'dataset-freeze.json').unlink()
    corpus.bind(store,store.root/'sessions/dev-01/registration.json',{'role':'development-validation'})
    with pytest.raises(ValueError,match='later role'):close(invalid_clock)
    (store.root/'sessions/dev-01/registration.json').unlink()
    corpus.bind(store,corpus.registration_path(store,'train-04'),{'fixture':True})
    with pytest.raises(ValueError,match='already begun'):close(invalid_clock)


@pytest.mark.parametrize('change',['restart','foreign','wrong-id','wrong-image'])
def test_current_original_context_must_be_identifiable(invalid_clock,change):
    actual=invalid_clock[3]
    if change=='restart':actual[0]['RestartCount']=1
    elif change=='foreign':actual[0]['Config']['Labels']['com.docker.compose.project']='foreign'
    elif change=='wrong-id':actual[0]['Id']='0'*64
    else:actual[0]['Image']='sha256:'+'0'*64
    with pytest.raises(ValueError):close(invalid_clock)


def test_original_object_mutation_and_observer_tampering_are_not_hidden(invalid_clock,monkeypatch):
    store,plan,session,_,_=invalid_clock
    close(invalid_clock)
    monkeypatch.setattr(clock,'_code_hash',lambda *_:'0'*64)
    with pytest.raises(ValueError,match='observer identity'):recovery.read_abandonment(store,session,plan)
    monkeypatch.setattr(clock,'_code_hash',lambda *_:'e'*64)
    raw=next((store.root/'runs/train-01-inventory-gradual/captures').glob('run-*/truth/raw/*.json'))
    raw.write_text('{}')
    with pytest.raises(ValueError,match='corrupt|changed'):recovery.read_abandonment(store,session,plan)


def test_fully_verified_positive_clock_group_cannot_be_replaced_by_quality_outcome(invalid_clock,monkeypatch):
    store,plan,session,_,_=invalid_clock
    build_slot(store,plan,session,'inventory-gradual',negative=False)
    monkeypatch.setattr(clock,'verify_capture',lambda *_a,**_k:{})
    with pytest.raises(ValueError,match='cannot be replaced based on outcomes'):close(invalid_clock)
    assert not (store.root/'sessions/train-01/closed.json').exists()


def test_clock_disposition_cannot_backdate_admission_cutoff(invalid_clock):
    from datetime import datetime,timezone
    store,plan,session,_,_=invalid_clock
    close(invalid_clock)
    with pytest.raises(ValueError,match='disposition'):
        recovery.read_abandonment(store,session,plan,as_of=datetime(2020,1,2,tzinfo=timezone.utc))


def test_partial_clock_source_cannot_substitute_boolean_for_numeric_recipe(invalid_clock):
    store,plan,session,_,_=invalid_clock
    path,run=build_slot(store,plan,session,'inventory-gradual',negative=True,partial=True)
    registration_path=path.parent.parent/'registration.json'
    _,registration=corpus.read_binding(store,registration_path)
    registration['recipe']['rps']=True
    registration_path.unlink();corpus.bind(store,registration_path,registration)
    run['parameters']['rps']=True
    ArtifactStore(path/'truth').write('manifests',run);(path/'run.json').write_text(json.dumps(run))
    with pytest.raises(ValueError,match='recipe differs'):close(invalid_clock)
