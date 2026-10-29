from datetime import timedelta
import json

import pytest

from sentinelops.contracts import utc_time
from sentinelops.ml.registry import ModelRegistry
from sentinelops.telemetry.core import stable_id
from sentinelops.workers.model_signals import collect_model_signals, add_model_support
from sentinelops.workers.incident_state import advance_groups
from sentinelops.workers.incidents import evaluate_cluster
from test_incident_worker import source_fixture, HEALTH, RCA, TOPOLOGY, PAY


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
