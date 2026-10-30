from copy import deepcopy
import json
from pathlib import Path

import pytest

from sentinelops.public_data.rcaeval_evaluation import diagnose_native, evaluate_development
from sentinelops.public_data.rcaeval_partitions import load_partitions
from sentinelops.incidents.rca import COMPONENTS, rank_native_support, unavailable

CONFIG = json.loads(Path("configs/rcaeval_development_evidence.json").read_text())
POLICY = json.loads(Path("configs/rca_policy.json").read_text())


def test_legacy_native_kernel_cannot_admit_live_business_log_methods():
    components = {name: unavailable() for name in COMPONENTS}
    components['log_support'] = {'value': 1., 'method': 'business-completion-support/v1',
                                 'evidence_refs': ['native-log'], 'missing_reason': None}
    with pytest.raises(ValueError, match='method/evidence'):
        rank_native_support(['native'], [{'id': 'native-log', 'type': 'log', 'service': 'native'}],
                            [{'service': 'native', 'components': components}], POLICY)


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


def test_canonical_ablations_remove_only_observed_support_and_preserve_policy():
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    from sentinelops.public_data.rcaeval_native import adapt_native_case
    from sentinelops.public_data.rcaeval_evaluation import diagnose_ablations, summarize_ablations

    def parquet(table):
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink)
        return sink.getvalue().to_pybytes()

    profile = json.loads(Path("configs/rcaeval_native_profile.json").read_text())
    scope = adapt_native_case({
        "metrics": parquet(pa.table({"time": pa.array([1000, 1001], type=pa.int64()),
                                    "native_cpu": pa.array([1., 2.], type=pa.float64())})),
        "logs": parquet(pa.table({"timestamp": pa.array([1001], type=pa.int64()),
                                  "container_name": pa.array(["native"], type=pa.large_string()),
                                  "message": pa.array(["RuntimeException"], type=pa.large_string())}))}, profile)
    before = deepcopy(scope)
    results = diagnose_ablations(scope, POLICY)
    assert scope == before
    full = results["native-traces-and-logs"]
    assert full["candidates"][0]["score"] == .15 and full["abstained"]
    assert results["without-native-logs"]["diagnostic_ranking"] == []
    assert results["without-native-traces"]["candidates"] == full["candidates"]
    assert all(value["qualified_root"] is None for value in results.values())
    metrics = summarize_ablations({"fixture": results}, {"fixture": {"root_cause_service": "native"},
        "quarantined": {"root_cause_service": "absent"}})
    assert metrics["native-traces-and-logs"]["top1"] == .5
    assert metrics["native-traces-and-logs"]["qualified_predictions"] == 0
    assert metrics["native-traces-and-logs"]["maximum_candidate_coverage_per_case"] == [.15, None]
