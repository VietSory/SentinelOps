"""Source admission negatives; synthetic rows are never an approved corpus."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from sentinelops.ml.local_data import audit_local_corpus, admitted_local_rows, freeze_local_corpus, _inside
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.experiments.local_corpus import load_plan, bind
from sentinelops.telemetry.store import ArtifactStore


def test_empty_corpus_reports_exact_zero_support_and_never_freezes(tmp_path):
    store = ArtifactStore(tmp_path)
    report, rows, cases = audit_local_corpus(store)
    assert report['status'] == 'BLOCKED' and not rows and not cases and report['holdout_opened'] is False
    for role in report['counts'].values():
        assert all(v['healthy_windows'] == v['healthy_groups'] == v['precursor_groups'] == 0 for v in role.values())
    assert any(c['condition'] == 'train/payment-service/healthy_windows' and c['required'] == 300 for c in report['failed_conditions'])
    assert any(c['condition'] == 'calibration/payment-service/healthy_windows' and c['required'] == 100 for c in report['failed_conditions'])
    with pytest.raises(DatasetNotReady): freeze_local_corpus(store, report)
    assert not (tmp_path/'dataset-freeze.json').exists()


def test_forged_ready_flags_do_not_replace_original_evidence(tmp_path):
    store = ArtifactStore(tmp_path)
    report, _, _ = audit_local_corpus(store)
    report.update(training_ready=True,status='READY_FOR_FITTING',failed_conditions=[])
    with pytest.raises(DatasetNotReady, match='raw-source/G2'):
        admitted_local_rows(store, report)


@pytest.mark.parametrize('path', ['../sealed-final/run.json','/tmp/foreign.json','runs/../../secret'])
def test_source_paths_cannot_escape_into_another_role_or_store(tmp_path,path):
    with pytest.raises(ValueError): _inside(tmp_path,path)


def test_source_symlink_is_not_admitted(tmp_path):
    (tmp_path/'other').mkdir()
    (tmp_path/'linked').symlink_to(tmp_path/'other',target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):
        _inside(tmp_path,'linked/run.json')


def test_sealed_registration_metadata_never_opens_raw_final_refs(tmp_path):
    store = ArtifactStore(tmp_path)
    bind(store,tmp_path/'sessions/sealed-final-01/registration.json',{
        'role':'sealed-final','unopened_raw_ref':'artifact://raw/'+'0'*64+'.json'})
    report, rows, _ = audit_local_corpus(store)
    assert not rows and report['holdout_opened'] is False
    assert report['registered_groups'] == 0


def test_local_report_cannot_fit_or_write_artifacts_before_g2(tmp_path):
    pytest.importorskip('numpy'); pytest.importorskip('sklearn'); pytest.importorskip('xgboost')
    from sentinelops.ml.models import train_local_http
    store = ArtifactStore(tmp_path/'corpus')
    report, _, _ = audit_local_corpus(store)
    ref = store.write('manifests',report)
    with pytest.raises(DatasetNotReady): train_local_http(store,ref,tmp_path/'model')
    assert not (tmp_path/'model').exists()


def test_verified_features_do_not_read_future_observations_or_backdate_availability(tmp_path):
    from datetime import timedelta
    from test_phase_incident_projection import source_fixture
    from sentinelops.ml.local_data import causal_features
    from sentinelops.telemetry.pipeline import process_captures
    from sentinelops.contracts import utc_time
    from sentinelops.telemetry.core import timestamp
    store, stream, batches, _ = source_fixture(tmp_path, [set()]*7)
    raw = [store.read('artifact://raw/'+p.name) for p in (store.root/'raw').glob('*.json')]
    ends = sorted({r['bucket_end'] for r in raw})
    prefix = [r for r in raw if r['bucket_end'] <= ends[-2]]
    before = process_captures(prefix,store,environment='fixture')
    after = process_captures(raw,store,environment='fixture')
    start = utc_time(ends[0])-timedelta(seconds=60)
    run = {'environment':'fixture','source_profile':before['source_profile'],'started_at':timestamp(start),
           'warmup_end_at':timestamp(start+timedelta(seconds=300)), 'ended_at':timestamp(utc_time(ends[-1])+timedelta(seconds=30))}
    a,_ = causal_features(store,{'normalization_manifest_ref':before['manifest_ref']},run)
    b,_ = causal_features(store,{'normalization_manifest_ref':after['manifest_ref']},run)
    chosen = [f for f in b if f['window_end'] == ends[-2]]
    assert a == chosen and a
    late = deepcopy(prefix)
    for r in late:
        if r['bucket_end'] == ends[-2]: r['collected_at'] = timestamp(utc_time(r['bucket_end'])+timedelta(seconds=61))
    changed = process_captures(late,store,environment='fixture')
    frames, excluded = causal_features(store,{'normalization_manifest_ref':changed['manifest_ref']},run)
    assert not any(f['window_end'] == ends[-2] for f in frames)
    assert excluded['outside-live-source-lateness-bound'] == 3


def test_actual_run_intervals_enforce_overlap_and_train_calibration_order():
    from datetime import timedelta
    from sentinelops.ml.local_data import validate_chronology
    start = datetime(2026,10,31,tzinfo=timezone.utc)
    end = start+timedelta(hours=1)
    assert validate_chronology({'train':[(start,end)],'calibration':[(end,end+timedelta(hours=1))]})
    with pytest.raises(ValueError, match='overlap'):
        validate_chronology({'train':[(start,end)],'calibration':[(start,end)]})
    with pytest.raises(ValueError, match='chronological'):
        validate_chronology({'calibration':[(start,end)],'train':[(end,end+timedelta(hours=1))]})
