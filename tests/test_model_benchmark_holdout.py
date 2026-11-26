from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from scripts.draft_benchmark_holdout import draft_matrix
from sentinelops.ml.model_benchmark_holdout import (
    verify_capture_inventory, verify_capture_source, verify_frozen_capture_sources,
)
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore
from test_local_labels import START, derive, fixture_oracle


def test_inventory_preserves_matrix_order_and_rejects_cross_group_run_reuse():
    draft = draft_matrix()
    matrix, pin = draft['matrix'], draft['matrix_sha256']
    candidates = 'a' * 64
    receipts = {}
    ordinal = 0
    for group in matrix['groups']:
        slots = []
        for slot in group['slots']:
            ordinal += 1
            slots.append(dict(slot_id=slot['slot_id'], run_id=f'run-{ordinal:032x}', status='completed',
                              source_manifest_sha256='b' * 64, oracle_certificate_sha256='c' * 64,
                              raw_refs=[f'artifact://raw/{ordinal:064x}.json']))
        receipts[group['group_id']] = dict(schema_version='benchmark-holdout-group/v1', role='sealed-final',
            group_id=group['group_id'], matrix_sha256=pin, candidates_sha256=candidates,
            source_profile=matrix['source_profile'], clock='arrival-aware', slots=slots[::-1])

    def verify(items):
        return verify_capture_inventory(matrix, items, matrix_sha256=pin, candidates_sha256=candidates)

    inventory = verify(dict(reversed(list(receipts.items()))))
    assert list(inventory) == [group['group_id'] for group in matrix['groups']]
    assert [item['slot_id'] for item in inventory['bench-final-01']] == [
        slot['slot_id'] for slot in matrix['groups'][0]['slots']]

    groups = deepcopy(receipts)
    groups['bench-final-02']['slots'][0]['run_id'] = groups['bench-final-01']['slots'][0]['run_id']
    with pytest.raises(ValueError, match='reuses a group or run'):
        verify(groups)
    groups = deepcopy(receipts)
    groups['bench-final-02']['group_id'] = 'ald-02'
    with pytest.raises(ValueError, match='source, frozen identity'):
        verify(groups)
    groups = deepcopy(receipts)
    groups['bench-final-02']['slots'].pop()
    with pytest.raises(ValueError, match='registered slots'):
        verify(groups)
    groups = deepcopy(receipts)
    groups['bench-final-02']['slots'][0]['status'] = 'interrupted'
    with pytest.raises(ValueError, match='incomplete holdout'):
        verify(groups)
    groups = deepcopy(receipts)
    groups['bench-final-02']['candidates_sha256'] = 'b' * 64
    with pytest.raises(ValueError, match='source, frozen identity'):
        verify(groups)


def test_holdout_source_rebuilds_request_oracle_and_detects_tampering(tmp_path, monkeypatch):
    import sentinelops.ml.model_benchmark_holdout as holdout

    group = draft_matrix()['matrix']['groups'][0]
    slot = group['slots'][0]
    run_id = 'run-' + '1' * 32
    run_dir = tmp_path / run_id
    serving = ArtifactStore(run_dir / 'serving')
    truth = ArtifactStore(run_dir / 'truth')
    certificates = ArtifactStore(tmp_path / 'certificates')
    opened, completed, outcomes = fixture_oracle()
    logs_ref = serving.write('raw', {'kind': 'serving-logs', 'records': completed})
    full_ref = truth.write('raw', {'kind': 'full-experiment-logs', 'records': opened})
    outcomes_ref = truth.write('raw', {'outcomes': outcomes})
    profile = 'prometheus-increase-60s-5s/v2'
    raw_ref = serving.write('raw', {'source_profile': profile})
    manifest_ref = serving.write('manifests', {'source_profile': profile, 'raw_refs': [raw_ref]})
    run = dict(run_id=run_id, session_id=group['group_id'], status='completed',
               declared_split='test', dirty_worktree=False, environment='local',
               source_profile=profile, source_commit='a' * 40,
               scenario_id=slot['recipe']['scenario'],
               parameters={**slot['recipe'], 'full_logs_ref': full_ref},
               source_manifest_ref=manifest_ref, logs_ref=logs_ref,
               request_outcomes_ref=outcomes_ref, started_at=timestamp(START),
               ended_at=timestamp(START + timedelta(minutes=14)))
    run_dir.mkdir(parents=True, exist_ok=True)
    run_path = run_dir / 'run.json'
    run_path.write_text(json.dumps(run))
    result_ref = certificates.write('manifests', {'status': 'completed', 'run_sha256': stable_id(run)})
    certificate = dict(certificate_version='local-http-oracle/v1', source_result_ref=result_ref,
                       source_logs_ref=logs_ref, source_full_logs_ref=full_ref,
                       source_client_ledger_ref=outcomes_ref, oracle=derive((opened, completed, outcomes)))
    oracle_ref = certificates.write('manifests', certificate)
    receipt = dict(slot_id=slot['slot_id'], run_id=run_id, source_manifest_sha256=manifest_ref[21:-5],
                   oracle_certificate_sha256=oracle_ref[21:-5], raw_refs=[raw_ref])
    replay = []
    monkeypatch.setattr(holdout, 'verify_source_replay', lambda store, manifest, document: replay.append(document['run_id']))
    kwargs = dict(group_id=group['group_id'], source_profile=profile,
                  source_commit='a' * 40, certificate_store=certificates)
    assert verify_capture_source(run_path, receipt, slot, **kwargs)['oracle_ref'] == oracle_ref
    assert replay == [run_id]
    altered = deepcopy(certificate)
    altered['oracle']['ended_at'] = timestamp(START + timedelta(minutes=13))
    forged_ref = certificates.write('manifests', altered)
    with pytest.raises(ValueError, match='does not reproduce'):
        verify_capture_source(run_path, {**receipt, 'oracle_certificate_sha256': forged_ref[21:-5]}, slot, **kwargs)
    with pytest.raises(ValueError, match='registered slot'):
        verify_capture_source(run_path, {**receipt, 'slot_id': 'foreign'}, slot, **kwargs)
    wrong_raw = serving.write('raw', {'source_profile': 'prometheus-increase-60s-5s/v1'})
    with pytest.raises(ValueError, match='raw references differ'):
        verify_capture_source(run_path, {**receipt, 'raw_refs': [wrong_raw]}, slot, **kwargs)
    original_raw = serving.root / raw_ref.removeprefix('artifact://')
    original_raw.write_text('{}')
    with pytest.raises(ValueError, match='checksum'):
        verify_capture_source(run_path, receipt, slot, **kwargs)


def test_source_verification_waits_for_both_freezes_and_all_metadata(tmp_path, monkeypatch):
    import sentinelops.ml.model_benchmark_holdout as holdout

    draft = draft_matrix()
    matrix = draft['matrix']
    candidate = dict(schema_version='benchmark-candidates-freeze/development-v1',
                     protocol_sha256='a' * 64, holdout_opened=False, activation_allowed=False,
                     lanes={'a': {'status': 'ready'}, 'b': {'status': 'ready'}})
    candidate_bytes = json.dumps(candidate).encode()
    pin = hashlib.sha256(candidate_bytes).hexdigest()
    receipts, run_paths = {}, {}
    for group in matrix['groups']:
        rows = []
        for index, slot in enumerate(group['slots']):
            identifier = group['ordinal'] * 100 + index
            rows.append(dict(slot_id=slot['slot_id'], run_id=f'run-{identifier:032x}', status='completed',
                source_manifest_sha256='b' * 64, oracle_certificate_sha256='c' * 64,
                raw_refs=[f'artifact://raw/{identifier:064x}.json']))
            run_paths[slot['slot_id']] = tmp_path / f'run-{identifier:032x}' / 'run.json'
        receipts[group['group_id']] = dict(schema_version='benchmark-holdout-group/v1', role='sealed-final',
            group_id=group['group_id'], matrix_sha256=draft['matrix_sha256'], candidates_sha256=pin,
            source_profile=matrix['source_profile'], clock='arrival-aware', slots=rows)
    touched = []
    monkeypatch.setattr(holdout, 'verify_protocol', lambda root: {'status': 'ready', 'protocol_sha256': 'a' * 64})
    monkeypatch.setattr(holdout, 'verify_capture_source', lambda *args, **kwargs: touched.append(args[1]['run_id']))
    kwargs = dict(matrix_sha256=draft['matrix_sha256'], candidates_sha256=pin,
                  capture_commit='f' * 40, certificate_store=None)
    with pytest.raises(ValueError, match='candidate freeze required'):
        verify_frozen_capture_sources(tmp_path, matrix, receipts, run_paths, **kwargs)
    assert not touched
    path = tmp_path / 'freeze/candidates.json'
    path.parent.mkdir()
    path.write_bytes(candidate_bytes)
    bad = deepcopy(receipts)
    bad['bench-final-05']['slots'][0]['status'] = 'interrupted'
    with pytest.raises(ValueError, match='incomplete holdout'):
        verify_frozen_capture_sources(tmp_path, matrix, bad, run_paths, **kwargs)
    assert not touched
    verify_frozen_capture_sources(tmp_path, matrix, receipts, run_paths, **kwargs)
    assert len(touched) == 35 and len(set(touched)) == 35
