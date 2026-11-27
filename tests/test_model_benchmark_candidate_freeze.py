from types import SimpleNamespace

import pytest

from scripts import freeze_benchmark_candidates as candidates
from sentinelops.ml import model_benchmark_detector_fit as detector_fit
from sentinelops.ml.model_benchmark_protocol import freeze_protocol
from test_model_benchmark_protocol import fixture


def test_candidate_freeze_requires_verified_inputs_and_is_immutable(tmp_path, monkeypatch):
    protocol, _ = fixture(tmp_path)
    evidence = tmp_path / 'lane-a-evidence.json'
    detector_pin = '1' * 64
    assert candidates.run(tmp_path, protocol, evidence, detector_pin)['status'] == 'blocked'
    output = tmp_path / 'freeze/candidates.json'
    assert not output.exists()
    freeze_protocol(protocol, tmp_path)
    assert candidates.run(tmp_path, protocol, evidence, detector_pin)['reasons'] == [
        'verified-detector-fit-required']
    detector_path = tmp_path / detector_fit.FIT_PATH
    detector_path.parent.mkdir(parents=True)
    detector_path.touch()
    verified = SimpleNamespace(status='ready', fit_sha256=detector_pin, persistence=2,
                               model=SimpleNamespace(digest='2' * 64), calibration={},
                               provenance_sha256=protocol['data']['manifest']['sha256'],
                               protocol_splits_sha256=None)

    def verify(root, *, expected_fit_sha256):
        assert root == tmp_path and expected_fit_sha256 == detector_pin
        return verified

    monkeypatch.setattr(detector_fit, 'verify_detector_fit', verify)
    monkeypatch.setattr(detector_fit, 'verify_protocol_detector_fit', lambda *args, **kwargs: verified)
    monkeypatch.setattr(candidates, '_lane_a', lambda *args: {'status': 'ready', 'thresholds': {'slope': .05}})
    assert candidates.run(tmp_path, protocol, evidence, detector_pin)['reasons'] == [
        'lane-b-protocol-split-binding-required']
    assert not output.exists()
    verified.protocol_splits_sha256 = protocol['data']['splits']['sha256']
    first = candidates.run(tmp_path, protocol, evidence, detector_pin)
    assert first['status'] == 'ready' and first['holdout_opened'] is False
    saved = output.read_bytes()
    assert candidates.run(tmp_path, protocol, evidence, detector_pin) == first
    verified.persistence = 3
    with pytest.raises(ValueError, match='already frozen'):
        candidates.run(tmp_path, protocol, evidence, detector_pin)
    assert output.read_bytes() == saved
