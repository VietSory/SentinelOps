"""ADR-016 online worker and replay using a disposable v3 numerical fixture."""
from datetime import timedelta
import json

import pytest

pytest.importorskip("pandas")

from sentinelops.ml.alibaba_bundle import package_fit
from sentinelops.ml.registry import ModelRegistry, ModelUnavailable
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health import HealthWorker
from sentinelops.workers.model_signals import collect_model_signals, verify_model_signal
from test_alibaba_bundle import synthetic_fit
from test_health_source import POLICY, START, capture


def test_shared_v3_bundle_infers_and_replays_only_after_accepted_history(tmp_path):
    serving = ArtifactStore(tmp_path / "serving")
    packaged = package_fit(synthetic_fit(tmp_path), serving.root / "bundle")
    manifest = json.loads((serving.root / "bundle/manifest.json").read_bytes())
    provenance = manifest["provenance"]
    evidence = serving.write("manifests", {"scope": "test-fixture"})
    approval = {
        "model_version": packaged["model_version"], "G2": "PASS", "G3": "PASS", "source_parity": "PASS",
        "activation_allowed": True, "evaluation_refs": [evidence], "source_profile": SOURCE_PROFILE_V2,
        "report_sha256": provenance["report_sha256"], "config_sha256": provenance["config_sha256"],
    }

    def release(record):
        approval_ref = serving.write("manifests", record)
        return serving.write("manifests", {"release_version": "validated-model-release/v1", "models": [
            {"bundle_directory": "bundle", "manifest_sha256": packaged["manifest_sha256"],
             "validation_ref": approval_ref}]})

    with pytest.raises(ModelUnavailable, match="source parity"):
        ModelRegistry.load(serving, release({**approval, "source_parity": "FAIL"}))
    registry = ModelRegistry.load(serving, release(approval))
    assert registry.available and registry.is_alibaba

    store = ArtifactStore(tmp_path / "source")
    worker = HealthWorker(None, store, POLICY, endpoint="http://127.0.0.1:9090",
                          environment="fixture", configuration_hash="a" * 64, source_profile=SOURCE_PROFILE_V2)
    raw_history, previous = [], None
    for index in range(10):
        end = START + timedelta(minutes=index)
        now = end + timedelta(seconds=40)
        current = [capture(service, end, profile=SOURCE_PROFILE_V2) for service in BUSINESS_ROUTES]
        for item in current:
            item["queries"]["duration_sum"]["response"]["data"]["result"][0]["value"][1] = (
                "6000" if index < 5 else "12000")
        raw_history.extend(current)
        previous = store.write("manifests", {
            "stream_id": worker.stream_id, "identity": worker.identity, "bucket_end": timestamp(end),
            "evaluated_at": timestamp(now), "previous_batch_ref": previous,
            "current_raw_refs": [store.write("raw", item) for item in current],
        })
        if index in (4, 8, 9):
            normalized = process_captures(raw_history[-15:], store, environment="fixture")
            signals = collect_model_signals(store, normalized, bucket_end=timestamp(end), registry=registry,
                                            now=now, batch_ref=previous)
            assert len(signals) == len(BUSINESS_ROUTES)
            for _, signal in signals:
                assert signal["signal_version"] == "validated-window-model/v3"
                envelope = store.read(signal["feature_ref"])
                verify_model_signal(envelope, signal, registry, store=store, batch_ref=previous)
                if index < 9:
                    assert signal["status"] == "UNAVAILABLE" and signal["model_feature_ref"] is None
                else:
                    assert signal["status"] == "AVAILABLE"
                    feature = store.read(signal["model_feature_ref"])
                    assert feature["features"]["rt_log_level_rel"] == pytest.approx(0.6931471805599453)
                    assert len(feature["raw_refs"]) == 10
                    with pytest.raises(ValueError, match="does not reproduce"):
                        verify_model_signal(envelope, {**signal, "model_feature_ref": signal["feature_ref"]},
                                            registry, store=store, batch_ref=previous)
