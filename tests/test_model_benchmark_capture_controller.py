import json

import pytest

from scripts.draft_benchmark_holdout import draft_matrix
from sentinelops.ml import model_benchmark_capture_controller as controller


def fixture_design(monkeypatch):
    draft = draft_matrix()
    matrix = draft['matrix']
    registration = {'capture_commit': 'a' * 40, 'matrix_sha256': draft['matrix_sha256'],
                    'candidates_sha256': 'b' * 64, 'registered_at': '2026-01-01T00:00:00Z'}
    monkeypatch.setattr(controller, 'load_capture_design', lambda *args, **kwargs: (registration, matrix, {}))
    return matrix, registration


def test_controller_runs_each_slot_once_and_resumes_completed_group(tmp_path, monkeypatch):
    matrix, _ = fixture_design(monkeypatch)
    group = matrix['groups'][0]
    group_dir = tmp_path / 'capture/groups' / group['group_id']
    monkeypatch.setattr(controller, 'prepare_group', lambda *args, **kwargs: {'status': 'ready'})
    def register(root, group_id, slot_id, *args, **kwargs):
        (group_dir / 'slots' / slot_id).mkdir(parents=True, exist_ok=True)
        return {'group_registration_sha256': 'c' * 64, 'slot_registration_sha256': 'd' * 64}
    monkeypatch.setattr(controller, 'register_slot', register)
    captures = []
    async def execute(config, *, output, **kwargs):
        run_id = f'run-{len(captures) + 1:032x}'
        path = output / run_id
        path.mkdir(parents=True)
        (path / 'run.json').write_text('{}')
        captures.append(kwargs['holdout_authorization']['slot_id'])
        return path
    monkeypatch.setattr(controller, 'execute', execute)
    def seal(root, group_id, slot_id, run_id, *args, **kwargs):
        receipt = {'slot_id': slot_id, 'run_id': run_id, 'status': 'completed',
                   'source_manifest_sha256': 'e' * 64, 'oracle_certificate_sha256': 'f' * 64,
                   'raw_refs': ['artifact://raw/' + run_id.removeprefix('run-').zfill(64) + '.json']}
        (group_dir / 'slots' / slot_id / 'receipt.json').write_text(json.dumps(receipt))
        return receipt
    monkeypatch.setattr(controller, 'seal_slot', seal)
    monkeypatch.setattr(controller, 'close_group', lambda *args, **kwargs: json.loads((group_dir / 'receipt.json').read_bytes()))
    result = controller.capture_group(tmp_path, group['group_id'], '1' * 64, project=tmp_path)
    assert captures == [slot['slot_id'] for slot in group['slots']]
    assert controller.capture_group(tmp_path, group['group_id'], '1' * 64, project=tmp_path) == result
    assert len(captures) == 7


def test_controller_never_retries_an_incomplete_slot(tmp_path, monkeypatch):
    matrix, _ = fixture_design(monkeypatch)
    group = matrix['groups'][0]
    slot = group['slots'][0]
    runs = tmp_path / 'capture/groups' / group['group_id'] / 'slots' / slot['slot_id'] / 'runs'
    (runs / ('run-' + '1' * 32)).mkdir(parents=True)
    monkeypatch.setattr(controller, 'prepare_group', lambda *args, **kwargs: {'status': 'ready'})
    with pytest.raises(ValueError, match='no automatic retry'):
        controller.capture_group(tmp_path, group['group_id'], '1' * 64, project=tmp_path)


def test_group_verification_checks_each_slot_source(tmp_path, monkeypatch):
    matrix, registration = fixture_design(monkeypatch)
    group = matrix['groups'][0]
    directory = tmp_path / 'capture/groups' / group['group_id']
    rows = []
    for index, slot in enumerate(group['slots']):
        row = {'slot_id': slot['slot_id'], 'run_id': f'run-{index + 1:032x}', 'status': 'completed',
               'source_manifest_sha256': 'e' * 64, 'oracle_certificate_sha256': 'f' * 64,
               'raw_refs': [f'artifact://raw/{index + 1:064x}.json']}
        slot_dir = directory / 'slots' / slot['slot_id']
        slot_dir.mkdir(parents=True)
        (slot_dir / 'receipt.json').write_text(json.dumps(row))
        rows.append(row)
    receipt = {'schema_version': 'benchmark-holdout-group/v1', 'role': 'sealed-final', 'group_id': group['group_id'],
               'matrix_sha256': registration['matrix_sha256'], 'candidates_sha256': registration['candidates_sha256'],
               'source_profile': matrix['source_profile'], 'clock': 'arrival-aware', 'slots': rows}
    (directory / 'receipt.json').write_text(json.dumps(receipt))
    checked = []
    monkeypatch.setattr(controller, 'verify_slot_context', lambda *args, **kwargs: checked.append(('context', args[3])))
    monkeypatch.setattr(controller, 'verify_capture_source', lambda *args, **kwargs: checked.append(('source', args[1]['run_id'])))
    assert controller.verify_group(tmp_path, group['group_id'], '1' * 64, project=tmp_path) == receipt
    assert len(checked) == 14
    path = directory / 'slots' / group['slots'][2]['slot_id'] / 'receipt.json'
    path.write_text('{}')
    with pytest.raises(ValueError, match='differs from its slot receipt'):
        controller.verify_group(tmp_path, group['group_id'], '1' * 64, project=tmp_path)


def test_close_group_stops_only_registered_containers_after_verification(tmp_path, monkeypatch):
    group_id = 'bench-final-01'
    directory = tmp_path / 'capture/groups' / group_id
    directory.mkdir(parents=True)
    group = {'context': {'containers': {'payment-service': {'container_id': 'one'},
                                        'inventory-service': {'container_id': 'two'}}}}
    (directory / 'registration.json').write_text(json.dumps(group))
    calls = []
    monkeypatch.setattr(controller, 'verify_group', lambda *args, **kwargs: calls.append('verified') or {'ok': True})
    monkeypatch.setattr(controller, 'configure_compose', lambda *args: calls.append('configured'))
    monkeypatch.setattr(controller, 'inspect_group_continuity', lambda *args: calls.append('inspected'))
    def docker(*args):
        calls.append(args)
        if args[:2] == ('ps', '-q'):
            return 'one\ntwo\n' if 'stopped' not in calls else ''
        assert args == ('stop', 'one', 'two')
        calls.append('stopped')
        return ''
    monkeypatch.setattr(controller, 'docker', docker)
    assert controller.close_group(tmp_path, group_id, '1' * 64, project=tmp_path) == {'ok': True}
    assert calls.index('verified') < calls.index('inspected') < calls.index(('stop', 'one', 'two'))
    monkeypatch.setattr(controller, 'verify_group', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('bad source')))
    calls.clear()
    with pytest.raises(ValueError, match='bad source'):
        controller.close_group(tmp_path, group_id, '1' * 64, project=tmp_path)
    assert calls == []


def test_seal_requires_complete_stopped_groups_and_replays_all_sources(tmp_path, monkeypatch):
    matrix, registration = fixture_design(monkeypatch)
    for group in matrix['groups']:
        directory = tmp_path / 'capture/groups' / group['group_id']
        rows = []
        for index, slot in enumerate(group['slots']):
            number = group['ordinal'] * 100 + index
            row = {'slot_id': slot['slot_id'], 'run_id': f'run-{number:032x}', 'status': 'completed',
                   'source_manifest_sha256': 'e' * 64, 'oracle_certificate_sha256': 'f' * 64,
                   'raw_refs': [f'artifact://raw/{number:064x}.json']}
            slot_dir = directory / 'slots' / slot['slot_id']
            slot_dir.mkdir(parents=True)
            (slot_dir / 'receipt.json').write_text(json.dumps(row))
            rows.append(row)
        receipt = {'schema_version': 'benchmark-holdout-group/v1', 'role': 'sealed-final', 'group_id': group['group_id'],
                   'matrix_sha256': registration['matrix_sha256'], 'candidates_sha256': registration['candidates_sha256'],
                   'source_profile': matrix['source_profile'], 'clock': 'arrival-aware', 'slots': rows}
        (directory / 'receipt.json').write_text(json.dumps(receipt))
    calls = []
    monkeypatch.setattr(controller, 'verify_frozen_capture_sources',
                        lambda *args, **kwargs: calls.append('verified') or [{}] * 35)
    monkeypatch.setattr(controller, 'docker', lambda *args: 'running\n')
    with pytest.raises(ValueError, match='source context is running'):
        controller.seal_capture(tmp_path, '1' * 64, project=tmp_path)
    assert calls == [] and not (tmp_path / 'capture/sealed.json').exists()
    monkeypatch.setattr(controller, 'docker', lambda *args: '')
    result = controller.seal_capture(tmp_path, '1' * 64, project=tmp_path)
    assert result['capture_sealed'] is True and result['slot_count'] == 35 and calls == ['verified']
    assert controller.seal_capture(tmp_path, '1' * 64, project=tmp_path) == result
    path = tmp_path / 'capture/groups' / matrix['groups'][2]['group_id'] / 'receipt.json'
    data = json.loads(path.read_bytes())
    data['slots'].pop()
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='registered slots'):
        controller.seal_capture(tmp_path, '1' * 64, project=tmp_path)
