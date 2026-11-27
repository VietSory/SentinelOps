import hashlib
import json
from datetime import datetime, timezone

import pytest

from scripts.draft_benchmark_holdout import draft_matrix
from sentinelops.ml import model_benchmark_forecast_inputs as inputs


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sealed_forecast_inputs_check_all_metadata_before_sources(tmp_path, monkeypatch):
    draft = draft_matrix()
    matrix = draft['matrix']
    candidate_pin, registration_pin = 'a' * 64, None
    registration = {'matrix_sha256': draft['matrix_sha256'], 'candidates_sha256': candidate_pin,
                    'capture_commit': 'b' * 40}
    registration_pin = write_json(tmp_path / 'capture/registration.json', registration)
    write_json(tmp_path / 'capture/matrix.json', matrix)
    monkeypatch.setattr(inputs, 'verify_candidate_freeze', lambda *args: {'protocol_sha256': 'c' * 64})
    monkeypatch.setattr(inputs, 'verify_capture_registration', lambda *args, **kwargs: registration)
    touched = []
    def replay(root, matrix, receipts, paths, **kwargs):
        touched.append('sources')
        return [{'group_id': group['group_id'], 'run_id': row['run_id'],
                 'source_result_ref': 'result', 'oracle_ref': 'oracle'}
                for group in matrix['groups'] for row in receipts[group['group_id']]['slots']]
    monkeypatch.setattr(inputs, 'verify_frozen_capture_sources', replay)
    with pytest.raises(ValueError, match='missing reference'):
        inputs.load_sealed_inputs(tmp_path, candidates_sha256=candidate_pin,
                                  registration_sha256=registration_pin, seal_sha256='d' * 64)
    assert touched == []
    pins = []
    for group in matrix['groups']:
        rows = []
        for index, slot in enumerate(group['slots']):
            number = group['ordinal'] * 100 + index
            rows.append({'slot_id': slot['slot_id'], 'run_id': f'run-{number:032x}', 'status': 'completed',
                'source_manifest_sha256': 'e' * 64, 'oracle_certificate_sha256': 'f' * 64,
                'raw_refs': [f'artifact://raw/{number:064x}.json']})
        receipt = {'schema_version': 'benchmark-holdout-group/v1', 'role': 'sealed-final',
                   'group_id': group['group_id'], 'matrix_sha256': draft['matrix_sha256'],
                   'candidates_sha256': candidate_pin, 'source_profile': matrix['source_profile'],
                   'clock': 'arrival-aware', 'slots': rows}
        pin = write_json(tmp_path / 'capture/groups' / group['group_id'] / 'receipt.json', receipt)
        pins.append({'group_id': group['group_id'], 'receipt_sha256': pin})
    seal = {'schema_version': 'benchmark-holdout-seal/v1', 'registration_sha256': registration_pin,
            'matrix_sha256': draft['matrix_sha256'], 'candidates_sha256': candidate_pin,
            'capture_commit': registration['capture_commit'], 'groups': pins, 'slot_count': 35,
            'capture_sealed': True, 'activation_allowed': False}
    seal_pin = write_json(tmp_path / 'capture/sealed.json', seal)
    result = inputs.load_sealed_inputs(tmp_path, candidates_sha256=candidate_pin,
                                       registration_sha256=registration_pin, seal_sha256=seal_pin)
    assert len(result['cases']) == 35 and touched == ['sources']
    assert [case['kind'] for case in result['cases'][:7]] == [slot['kind'] for slot in matrix['groups'][0]['slots']]
    (tmp_path / 'capture/groups/bench-final-05/receipt.json').write_text('{}')
    touched.clear()
    with pytest.raises(ValueError, match='changed reference'):
        inputs.load_sealed_inputs(tmp_path, candidates_sha256=candidate_pin,
                                  registration_sha256=registration_pin, seal_sha256=seal_pin)
    assert touched == []


def test_source_topology_uses_pinned_commit_and_excludes_unobserved_nodes(tmp_path, monkeypatch):
    from sentinelops.ml.ltfp_graph import build_graph_input
    from sentinelops.telemetry.core import stable_id

    commit = 'a' * 40
    data = b'version: 1\ndependencies:\n  checkout-service: [payment-service, inventory-service]\n  payment-service: [rds]\n  inventory-service: [rds]\n  rds: []\n'
    calls = []
    def git(args, **kwargs):
        calls.append(args)
        return 'b' * 40 + '\n' if kwargs.get('text') else data
    monkeypatch.setattr(inputs.subprocess, 'check_output', git)
    run = {'source_commit': commit, 'dirty_worktree': False, 'environment': 'local',
           'started_at': '2026-01-01T00:00:00Z'}
    projection = inputs.source_topology(run, project=tmp_path, capture_commit=commit)
    assert projection['topology']['directed_edges'] == [
        ['checkout-service', 'inventory-service'], ['checkout-service', 'payment-service']]
    assert projection['excluded_edges'] == [['inventory-service', 'rds'], ['payment-service', 'rds']]
    assert projection['topology']['identity']['sha256'] == hashlib.sha256(data).hexdigest()
    assert all(args[-1] == commit + ':configs/topology.yaml' for args in calls)
    config = {'window_minutes': 5, 'baseline': {'max_minutes': 60, 'min_minutes': 30},
              'features': ['a', 'b', 'c', 'd', 'e', 'f']}
    point = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)
    graph = build_graph_input(projection['node_ids'], {}, anchor=point, as_of=point, config=config,
                              topology=projection['topology'], source_identity={'scope': 'fixture'})
    assert graph['graph_sha256'] == projection['graph_sha256'] == stable_id(graph['topology'])
    with pytest.raises(ValueError, match='registered clean capture source'):
        inputs.source_topology({**run, 'dirty_worktree': True}, project=tmp_path, capture_commit=commit)
