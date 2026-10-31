from datetime import timedelta
import json

import pytest

from sentinelops.contracts import utc_time
from sentinelops.ml.registry import ModelRegistry
from sentinelops.telemetry.core import stable_id
from sentinelops.workers.model_signals import collect_model_signals, add_model_support, verify_model_evidence
from sentinelops.workers.incident_state import advance_groups
from sentinelops.workers.legacy_incident_projection import evaluate_cluster
from test_phase_incident_projection import source_fixture, HEALTH, RCA, TOPOLOGY, PAY
from sentinelops.workers.legacy_incident_projection import incident_commands
from sentinelops.telemetry.core import FEATURE_KEYS


def test_absent_models_leave_null_inference_and_stable_signal_ids(tmp_path):
    store, stream, batches, records = source_fixture(tmp_path, [{PAY}] * 5)
    batch = store.read(batches[-1]["manifest_ref"])
    normal = store.read(batch["normalization_manifest_ref"])
    now = utc_time(batch["evaluated_at"])
    signals = collect_model_signals(store, normal, bucket_end=batch["bucket_end"], registry=ModelRegistry(), now=now)
    assert len(signals) == 3
    assert all(s["status"] == "UNAVAILABLE" and s["inference"] is None for _, s in signals)
    assert signals == collect_model_signals(store, normal, bucket_end=batch["bucket_end"], registry=ModelRegistry(), now=now)
    later = collect_model_signals(store, normal, bucket_end=batch["bucket_end"], registry=ModelRegistry(), now=now + timedelta(seconds=1))
    assert [s["signal_id"] for _, s in signals] == [s["signal_id"] for _, s in later]


def test_invalid_model_output_is_rejected_instead_of_attached_to_rca(tmp_path):
    store, stream, batches, records = source_fixture(tmp_path, [{PAY}] * 5)
    batch = store.read(batches[-1]["manifest_ref"])
    normal = store.read(batch["normalization_manifest_ref"])
    class InvalidRegistry:
        identity = "fixture-invalid"
        def infer_envelope(self, envelope, now):
            return {"detection": {"anomaly_score": float("nan")}, "risk": None}
    with pytest.raises(ValueError):
        collect_model_signals(store, normal, bucket_end=batch["bucket_end"], registry=InvalidRegistry(), now=utc_time(batch["evaluated_at"]))


def test_real_fixture_inference_attaches_exact_window_and_preserves_policy_abstention(tmp_path):
    np = pytest.importorskip("numpy")
    from sentinelops.ml.models import fit_detector
    store, stream, batches, records = source_fixture(tmp_path, [{PAY}] * 4)
    state, closed = None, []
    for row in batches:
        batch = store.read(row["manifest_ref"])
        objects = [(r["signal_ref"], json.loads(r["payload"])) for r in records if r["bucket_end"] == row["bucket_end"]]
        state, emitted = advance_groups(state, batch, objects, TOPOLOGY, HEALTH)
        closed.extend(emitted)
    assert len(closed) == 1
    rng = np.random.default_rng(42)
    detector = fit_detector(rng.normal(size=(300, 5)), rng.normal(size=(100, 5)), FEATURE_KEYS)
    registry = ModelRegistry(); registry.identity = "fixture-numerical-only"
    registry._models[PAY] = ({"model_version": "fixture-not-approved", "ordered_features": list(FEATURE_KEYS),
        "provenance": {"source_profile": stream and json.loads(stream["identity"])["source_profile"]}}, detector, None, "fixture://not-validation")
    now = utc_time(batch["evaluated_at"])
    signals = collect_model_signals(store, store.read(batch["normalization_manifest_ref"]), bucket_end=batch["bucket_end"], registry=registry, now=now)
    available = next(s for _, s in signals if s["service"] == PAY)
    assert available["status"] == "AVAILABLE" and available["inference"]["risk"] is None
    row = evaluate_cluster(store, closed[0], policy=RCA, as_of=now, health_policy=HEALTH)[0]
    enriched = add_model_support(store, row, signals, policy=RCA, as_of=now)
    payment = next(c for c in enriched["rca"]["candidates"] if c["service"] == PAY)
    assert payment["components"]["anomaly_strength"]["value"] == available["inference"]["detection"]["anomaly_score"]
    assert enriched["rca"]["abstained"] and enriched["rca"]["actions_allowed"] is False
    commands = incident_commands("a" * 64, closed[0], [enriched], batch["evaluated_at"], signals)
    assert commands[-1].payload["service_signals"][0]["degradation_risk"] is None
    with pytest.raises(ValueError, match="duplicate"):
        add_model_support(store, row, signals + signals, policy=RCA, as_of=now)
    state_ref = store.write("manifests", {"last_batch_ref": batches[-1]["manifest_ref"]})
    capture = {"profile": {"model_release_ref": registry.identity}, "window_start": closed[0]["scope"]["first_onset_at"],
        "window_end": batch["bucket_end"], "analyzed_at": batch["evaluated_at"], "model_evidence": {"release_ref": registry.identity,
            "source_state_ref": state_ref, "feature_batch_ref": batches[-1]["manifest_ref"], "signals": signals}}
    assert verify_model_evidence(store, capture, registry) == signals
    from copy import deepcopy
    tampered = deepcopy(capture)
    selected = next(item for item in tampered["model_evidence"]["signals"] if item[1]["service"] == PAY)
    selected[1]["inference"]["detection"]["anomaly_score"] = .123456
    index = tampered["model_evidence"]["signals"].index(selected)
    tampered["model_evidence"]["signals"][index] = (store.write("signals", selected[1]), selected[1])
    with pytest.raises(ValueError, match="estimator"):
        verify_model_evidence(store, tampered, registry)


@pytest.fixture
def timed_model_capture(tmp_path):
    """Real estimator on synthetic numbers; timing fixture, never model admission."""
    np = pytest.importorskip('numpy')
    from sentinelops.ml.models import fit_detector
    store, stream, batches, _ = source_fixture(tmp_path, [{PAY}] * 5)
    batch = store.read(batches[-1]['manifest_ref'])
    normal = store.read(batch['normalization_manifest_ref'])
    rng = np.random.default_rng(42)
    detector = fit_detector(rng.normal(size=(300, 5)), rng.normal(size=(100, 5)), FEATURE_KEYS)
    registry = ModelRegistry()
    registry.identity = 'fixture-clock-only'
    registry._models[PAY] = ({'model_version': 'fixture-not-approved', 'ordered_features': list(FEATURE_KEYS),
        'provenance': {'source_profile': json.loads(stream['identity'])['source_profile']}}, detector, None, 'fixture://not-validation')
    start = utc_time(batch['bucket_end']) + timedelta(seconds=179)
    finish = start + timedelta(seconds=2)
    state_ref = store.write('manifests', {'last_batch_ref': batches[-1]['manifest_ref']})
    capture = {'profile': {'model_release_ref': registry.identity}, 'window_start': batch['bucket_end'],
        'window_end': batch['bucket_end'], 'analyzed_at': finish.isoformat(), 'model_evidence': {
            'release_ref': registry.identity, 'source_state_ref': state_ref,
            'feature_batch_ref': batches[-1]['manifest_ref'], 'signals': []}}
    return store, normal, batch, registry, start, finish, capture


def clock_signals(context, *, legacy=False):
    store, normal, batch, registry, start, finish, capture = context
    signals = collect_model_signals(store, normal, bucket_end=batch['bucket_end'], registry=registry, now=start)
    changed = []
    for _, signal in signals:
        if legacy:
            signal['signal_version'] = 'validated-window-model/v1'
            signal.pop('inference_started_at', None)
        else:
            signal.update(signal_version='validated-window-model/v2', inference_started_at=start.isoformat(), evaluated_at=finish.isoformat())
            if signal['inference'] is not None:
                signal['inference']['inferred_at'] = finish.isoformat()
        changed.append((store.write('signals', signal), signal))
    capture['model_evidence']['signals'] = changed
    return changed


def test_inference_crossing_freshness_boundary_replays_at_actual_start(timed_model_capture):
    store, _, _, registry, _, _, capture = timed_model_capture
    signals = clock_signals(timed_model_capture)
    assert next(s for _, s in signals if s['service'] == PAY)['status'] == 'AVAILABLE'
    assert verify_model_evidence(store, capture, registry) == signals


def test_legacy_model_signal_remains_replayable(timed_model_capture):
    store, _, _, registry, _, _, capture = timed_model_capture
    signals = clock_signals(timed_model_capture, legacy=True)
    assert verify_model_evidence(store, capture, registry) == signals


@pytest.mark.parametrize('change', ['finish-after-evaluation', 'start-after-finish', 'start-before-availability',
                                   'false-status', 'unknown-version', 'missing-start'])
def test_model_signal_rejects_forged_clock_or_state(timed_model_capture, change):
    store, _, _, registry, start, finish, capture = timed_model_capture
    signals = clock_signals(timed_model_capture)
    index = next(i for i, (_, s) in enumerate(signals) if s['service'] == PAY)
    signal = signals[index][1]
    if change == 'finish-after-evaluation': signal['inference']['inferred_at'] = (finish + timedelta(seconds=1)).isoformat()
    if change == 'start-after-finish': signal['inference_started_at'] = (finish + timedelta(seconds=1)).isoformat()
    if change == 'start-before-availability': signal['inference_started_at'] = (utc_time(signal['window_end']) - timedelta(seconds=1)).isoformat()
    if change == 'false-status': signal['status'] = 'UNAVAILABLE'
    if change == 'unknown-version': signal['signal_version'] = 'validated-window-model/v999'
    if change == 'missing-start': signal.pop('inference_started_at')
    signals[index] = (store.write('signals', signal), signal)
    with pytest.raises(ValueError):
        verify_model_evidence(store, capture, registry)


def test_collector_records_actual_start_and_completion_for_replay(timed_model_capture, monkeypatch):
    from sentinelops.workers import model_signals as module
    store, normal, batch, registry, start, finish, capture = timed_model_capture
    ticks = iter([start, finish] * 3)
    class Clock:
        @staticmethod
        def now(zone):
            return next(ticks)
    monkeypatch.setattr(module, 'datetime', Clock)
    signals = collect_model_signals(store, normal, bucket_end=batch['bucket_end'], registry=registry)
    available = next(s for _, s in signals if s['service'] == PAY)
    assert available['signal_version'] == 'validated-window-model/v2'
    assert utc_time(available['inference_started_at']) == start
    assert utc_time(available['inference']['inferred_at']) == finish
    assert utc_time(available['evaluated_at']) == finish
    capture['model_evidence']['signals'] = signals
    assert verify_model_evidence(store, capture, registry) == signals


def test_future_feature_is_recorded_unavailable_and_replays(timed_model_capture):
    from sentinelops.workers.model_signals import verify_model_signal
    store, normal, batch, registry, start, _, _ = timed_model_capture
    original = next(store.read(ref) for ref in normal['feature_refs']
                    if store.read(ref)['service'] == PAY and store.read(ref)['window_end'] == batch['bucket_end'])
    original['as_of'] = (start + timedelta(seconds=1)).isoformat()
    reference = store.write('features', original)
    signals = collect_model_signals(store, {'feature_refs': [reference]}, bucket_end=batch['bucket_end'], registry=registry, now=start)
    signal = signals[0][1]
    assert signal['status'] == 'UNAVAILABLE' and signal['inference'] is None
    verify_model_signal(original, signal, registry)


def test_completed_v2_risk_is_suppressed_and_replays_across_anchor_minute(timed_model_capture, monkeypatch):
    """Numerical software fixture; no model/corpus promotion."""
    from sentinelops.workers import model_signals as module
    from sentinelops.telemetry.core import timestamp
    from sentinelops.ml.models import fit_risk
    import numpy as np
    store, normal, batch, registry, _, _, capture = timed_model_capture
    manifest, detector, _, validation = registry._models[PAY]
    sample = np.random.default_rng(42).normal(size=(300, 5))
    risk = fit_risk(sample, (sample[:, 0] > 0).astype(int), FEATURE_KEYS)
    manifest.update(origin='admitted-local-http',risk_thresholds={'watch':.5,'degrading':.8})
    registry._models[PAY] = (manifest,detector,risk,validation)
    end = utc_time(batch['bucket_end'])
    start, finish = end + timedelta(seconds=59), end + timedelta(seconds=61)
    ticks = iter([start, finish] * 3)
    class Clock:
        @staticmethod
        def now(zone): return next(ticks)
    monkeypatch.setattr(module,'datetime',Clock)
    capture['analyzed_at'] = timestamp(finish)
    signals = collect_model_signals(store,normal,bucket_end=batch['bucket_end'],registry=registry)
    signal = next(s for _,s in signals if s['service'] == PAY)
    assert signal['status'] == 'AVAILABLE' and signal['inference']['detection'] is not None
    assert signal['inference']['risk'] is None
    assert signal['inference']['risk_availability']['missing_reason'] == 'forecast-anchor-minute-advanced'
    capture['model_evidence']['signals'] = signals
    assert verify_model_evidence(store,capture,registry) == signals
