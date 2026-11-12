"""Regression tests for lane B v2 profile propagation (fixes 666e62e, 029b265 from local-nixos-host)."""
import ast
from pathlib import Path

import pytest

from scripts import host_runtime, lane_b_host as host

V2 = 'prometheus-increase-60s-5s/v2'
ROOT = Path(__file__).resolve().parents[1]


class Captured(Exception):
    pass


@pytest.mark.parametrize('session,expected', [
    ({'health_source_profile': V2, 'arguments': ['--health-stream', 's']}, V2),  # incident role: metadata only
    ({'arguments': ['--source-profile', V2]}, V2),                                # health role: explicit flag
    ({'arguments': []}, None),                                                    # v1 session keeps the default
])
def test_runtime_identity_uses_the_selected_health_profile(monkeypatch, tmp_path, session, expected):
    import sentinelops.telemetry.prometheus as prometheus
    import sentinelops.workers.health as health

    class Source:
        def __init__(self, endpoint): pass
        def verify_configuration(self): return {'configuration_hash': 'h'}
        def close(self): pass

    def worker(*args, **kwargs):
        raise Captured(kwargs.get('source_profile'))
    monkeypatch.setattr(prometheus, 'PrometheusSource', Source)
    monkeypatch.setattr(health, 'HealthWorker', worker)
    (tmp_path / 'configs').mkdir()
    (tmp_path / 'configs/slo_health_policy.json').write_text('{}')
    with pytest.raises(Captured) as caught:
        host_runtime.health_identity(tmp_path, {**session, 'health_store': str(tmp_path / 'store')})
    assert caught.value.args[0] == expected


@pytest.mark.parametrize('session,expected', [
    ({'health_source_profile': V2}, V2), ({'arguments': ['--source-profile', V2]}, V2), ({'arguments': []}, None)])
def test_incident_start_records_the_running_health_profile(monkeypatch, tmp_path, session, expected):
    monkeypatch.setattr(host, 'state', lambda role: {'state': 'RUNNING', 'unit_file': str(tmp_path / 'unit.service')})
    monkeypatch.setattr(host, 'private_json', lambda path: session)
    assert host.health_source_profile() == expected
    monkeypatch.setattr(host, 'state', lambda role: {'state': 'NOT_INSTALLED'})
    assert host.health_source_profile() is None


def test_host_release_bundles_the_alibaba_feature_config_the_registry_reads():
    from sentinelops.ml import alibaba_training
    tree = ast.parse((ROOT / 'scripts/lane_b_host.py').read_text())
    build = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build')
    copied = {c.value for c in ast.walk(build) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    assert alibaba_training.CONFIG.name in copied


def test_live_check_recheck_derives_the_consumer_with_the_selected_profile():
    tree = ast.parse((ROOT / 'scripts/check_lane_b_live.py').read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, 'id', None) == 'current_consumer']
    assert calls and all(any(k.arg == 'source_profile' for k in call.keywords) for call in calls)
