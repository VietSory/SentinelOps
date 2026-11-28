import json
from types import SimpleNamespace

import pytest

from scripts import freeze_benchmark_candidates as candidates
from sentinelops.ml import model_benchmark_detector_fit as detector_fit
from sentinelops.ml.model_benchmark_protocol import freeze_protocol
from sentinelops.public_data.alibaba import file_sha256
from test_model_benchmark_protocol import fixture


@pytest.mark.parametrize('lanes', [('a', 'b'), ('b',)])
def test_candidate_freeze_requires_verified_inputs_and_is_immutable(tmp_path, monkeypatch, lanes):
    protocol, _ = fixture(tmp_path)
    evidence = tmp_path / 'lane-a-evidence.json' if 'a' in lanes else None
    detector_pin = '1' * 64
    assert candidates.run(tmp_path, protocol, evidence, detector_pin)['status'] == 'blocked'
    output = tmp_path / 'freeze/candidates.json'
    assert not output.exists()
    freeze_protocol(protocol, tmp_path, lanes=lanes)
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
    def verify_lane_a(*args):
        assert 'a' in lanes
        return {'status': 'ready', 'thresholds': {'slope': .05}}

    monkeypatch.setattr(candidates, '_lane_a', verify_lane_a)
    assert candidates.run(tmp_path, protocol, evidence, detector_pin)['reasons'] == [
        'lane-b-protocol-split-binding-required']
    assert not output.exists()
    verified.protocol_splits_sha256 = protocol['data']['splits']['sha256']
    assert candidates.run(tmp_path, protocol, evidence, detector_pin)['reasons'] == ['combo-operating-point-missing']
    combo = {'status': 'ready', 'threshold': 1.0, 'budget': .01,
             'thresholds': {'median_mad': 0., 'adsketch': None}, 'selection_rule': '3' * 64}
    verified.calibration['adsketch_mad_or'] = combo
    assert candidates.run(tmp_path, protocol, evidence, detector_pin)['reasons'] == ['combo-operating-point-invalid']
    combo['thresholds']['median_mad'] = .25
    assert candidates.run(tmp_path, protocol, evidence, detector_pin)['reasons'] == ['combo-selection-rule-mismatch']
    assert not output.exists()
    combo['selection_rule'] = protocol['reporting']['lane_b']['combo_selection_rule_id']
    for field, invalid in [('threshold', .5), ('budget', .02)]:
        original = combo[field]
        combo[field] = invalid
        assert candidates.run(tmp_path, protocol, evidence, detector_pin)['reasons'] == ['combo-operating-point-invalid']
        assert not output.exists()
        combo[field] = original
    first = candidates.run(tmp_path, protocol, evidence, detector_pin)
    assert first['status'] == 'ready' and first['holdout_opened'] is False
    saved = output.read_bytes()
    manifest = json.loads(saved)
    assert set(manifest['lanes']) == set(lanes)
    assert manifest['lanes']['b']['operating_points']['adsketch_mad_or'] == combo
    assert candidates.run(tmp_path, protocol, evidence, detector_pin) == first
    verified.persistence = 3
    with pytest.raises(ValueError, match='already frozen'):
        candidates.run(tmp_path, protocol, evidence, detector_pin)
    assert output.read_bytes() == saved


def test_lane_a_candidate_freeze_skips_an_unfrozen_lane_b(tmp_path, monkeypatch):
    from sentinelops.ml.model_benchmark_holdout import verify_candidate_freeze

    protocol, _ = fixture(tmp_path)
    protocol['checks'] = [check for check in protocol['checks'] if check['test_id'] != 'B06']
    freeze_protocol(protocol, tmp_path, lanes=('a',))
    monkeypatch.setattr(detector_fit, 'verify_detector_fit', lambda *args, **kwargs: pytest.fail('lane B read'))
    assert candidates.run(tmp_path, protocol, None, None)['reasons'] == ['lane-a-evidence-required']
    monkeypatch.setattr(candidates, '_lane_a', lambda *args: {'status': 'ready', 'thresholds': {'slope': .05}})
    result = candidates.run(tmp_path, protocol, tmp_path / 'lane-a-evidence.json', None)
    assert result['status'] == 'ready'
    manifest = json.loads((tmp_path / 'freeze/candidates.json').read_bytes())
    assert manifest['frozen_lanes'] == ['a'] and set(manifest['lanes']) == {'a'}
    assert verify_candidate_freeze(tmp_path, file_sha256(tmp_path / 'freeze/candidates.json'))['frozen_lanes'] == ['a']


def test_candidate_freeze_blocks_pinned_fit_without_combo_even_if_verification_rejects_it(tmp_path):
    protocol, _ = fixture(tmp_path)
    assert freeze_protocol(protocol, tmp_path, lanes=('b',))['status'] == 'ready'
    fit = tmp_path / detector_fit.FIT_PATH
    fit.parent.mkdir(parents=True)
    fit.write_text(json.dumps({'calibration': {'median_mad': {}, 'adsketch': {}, 'isolation_forest': {}}}))
    with pytest.raises(ValueError, match='detector fit byte SHA256 mismatch'):
        candidates.run(tmp_path, protocol, None, '0' * 64)
    assert candidates.run(tmp_path, protocol, None, file_sha256(fit))['reasons'] == ['combo-operating-point-missing']
    assert not (tmp_path / 'freeze/candidates.json').exists()


@pytest.mark.parametrize('rule, reason', [
    ('event', 'event-rule-id-mismatch'), ('combo', 'combo-rule-id-mismatch'),
])
def test_candidate_freeze_revalidates_current_rule_id_before_reading_fit(tmp_path, monkeypatch, rule, reason):
    from sentinelops.ml import model_benchmark_alibaba_events as events
    from sentinelops.ml import model_benchmark_detector_combo as combo

    protocol, _ = fixture(tmp_path)
    assert freeze_protocol(protocol, tmp_path, lanes=('b',))['status'] == 'ready'
    if rule == 'event':
        monkeypatch.setattr(events, 'ALIBABA_EVENT_RULE_ID', '0' * 64)
    else:
        monkeypatch.setattr(combo, 'COMBO_SELECTION_RULE_ID', '0' * 64)
    monkeypatch.setattr(detector_fit, 'verify_detector_fit', lambda *args, **kwargs: pytest.fail('fit read'))
    result = candidates.run(tmp_path, protocol, None, '1' * 64)
    assert result == {'status': 'blocked', 'reasons': ['b: ' + reason]}
    assert not (tmp_path / 'freeze/candidates.json').exists()
