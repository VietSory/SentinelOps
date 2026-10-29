from copy import deepcopy
import json
from pathlib import Path

import pytest

from sentinelops.public_data.rcaeval_evaluation import diagnose_native, evaluate_development
from sentinelops.public_data.rcaeval_partitions import load_partitions

CONFIG = json.loads(Path("configs/rcaeval_development_evidence.json").read_text())
POLICY = json.loads(Path("configs/rca_policy.json").read_text())


def test_native_evidence_ablation_reuses_weights_and_never_invents_slo_or_model():
    logs = [{"timestamp": 100, "container_name": "native-payment", "message": "request failed"},
            {"timestamp": 101, "container_name": "native-checkout", "message": "request completed"}]
    result = diagnose_native([100, 101], ["native-payment_cpu", "native-checkout_latency_p95"], logs, [], CONFIG, POLICY,
                             {"logs": "public-file-sha256://fixture"})
    full = result["result"]
    assert full["ranking"] == ["native-payment"] and full["abstained"] is True
    assert next(c["score"] for c in full["candidates"] if c["service"] == "native-payment") == .15
    assert result["ablations"]["without-native-log-support"]["ranking"] == []
    assert result["modality_counts"]["error_token_matches"] == 1
    assert all(c["components"]["anomaly_strength"]["value"] is None for c in full["candidates"])
    assert "native-checkout_latency" not in {c["service"] for c in full["candidates"]}


def test_unknown_or_future_native_log_does_not_create_support():
    logs = [{"timestamp": 102, "container_name": "native", "message": "error"}]
    result = diagnose_native([100, 101], ["native_cpu"], logs, [], CONFIG, POLICY, {})
    assert result["result"]["ranking"] == [] and result["result"]["abstained"]
    assert result["modality_counts"]["invalid_log_times"] == 1


@pytest.mark.parametrize("role", ["sealed-holdout", "validation"])
def test_native_development_rejects_other_roles_before_any_raw_access(tmp_path, role):
    config = deepcopy(CONFIG)
    config["cases"] = next(g["cases"][:1] for g in load_partitions()["assignments"] if g["role"] == role)
    with pytest.raises(ValueError):
        evaluate_development(tmp_path / "nonexistent-manifest.json", config, POLICY)
