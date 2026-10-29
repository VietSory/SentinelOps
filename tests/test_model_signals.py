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
