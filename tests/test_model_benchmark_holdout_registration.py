import json

from scripts import register_benchmark_capture as registration
from sentinelops.telemetry.core import BUSINESS_ROUTES


def test_registration_blocks_before_source_reads_without_capture_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(registration, '_capture_adapter_ready', lambda: False)
    monkeypatch.setattr(registration, 'verify_candidate_freeze', lambda *args: (_ for _ in ()).throw(AssertionError()))
    result = registration.run(tmp_path, candidates_sha256='a' * 64, pilot_root=tmp_path / 'pilot',
                              pilot_ordinal=2, capture_project=tmp_path)
    assert result == {'status': 'blocked', 'reasons': ['holdout-capture-adapter-required']}
    assert not list(tmp_path.iterdir())


def test_registration_is_prospective_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(registration, '_capture_adapter_ready', lambda: True)
    monkeypatch.setattr(registration, 'clean_source', lambda project: 'f' * 40)
    monkeypatch.setattr(registration, 'verify_candidate_freeze', lambda root, pin: {'protocol_sha256': 'a' * 64})
    (tmp_path / 'freeze').mkdir()
    (tmp_path / 'freeze/protocol.json').write_bytes(b'frozen-protocol')
    pilot_root = tmp_path / 'pilot'
    pilot_root.mkdir()
    def case(number):
        return {'run_id': f'run-{number:032x}', 'control_steps': 3,
                'source_result_ref': f'artifact://manifests/{number:064x}.json',
                'oracle_ref': f'artifact://manifests/{number + 10:064x}.json',
                'services': {name: {'verified_minutes': 20, 'unknown_minutes': 0, 'latency_episodes': 0}
                             for name in BUSINESS_ROUTES}}
    pilot = {'schema_version': 'benchmark-low-ramp-pilot/development-v1', 'status': 'ready',
             'maximum_delay_ms': 400, 'checker_source_commit': 'f' * 40, 'holdout_opened': False,
             'activation_allowed': False, 'cases': {'payment-gradual': case(1), 'inventory-gradual': case(2)}}
    def verify_pilot(root, *, ordinal, project, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(pilot))
        return pilot
    monkeypatch.setattr(registration, 'verify_pilot', verify_pilot)
    kwargs = {'candidates_sha256': 'b' * 64, 'pilot_root': pilot_root, 'pilot_ordinal': 2,
              'capture_project': tmp_path}
    first = registration.run(tmp_path, **kwargs)
    assert first['status'] == 'ready' and first['holdout_opened'] is False
    assert registration.run(tmp_path, **kwargs) == first
    saved = json.loads((tmp_path / 'capture/registration.json').read_bytes())
    assert saved['capture_commit'] == 'f' * 40 and saved['matrix_sha256'] == first['matrix_sha256']
