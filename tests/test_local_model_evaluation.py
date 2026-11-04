"""Numerical fixtures exercise evaluation software, never source admission or G3."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from sentinelops.ml.local_evaluation import (
    evaluate_local_models, load_protocol, paired_group_intervals, persistent_states, risk_curve, summarize,
)
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.telemetry.store import ArtifactStore


def at(minute, second=0):
    return f"2026-11-01T00:{minute:02d}:{second:02d}Z"


def case_and_predictions(group="dev-01"):
    case = {"run_id": "run-" + group, "group_id": group, "role": "development-validation",
            "ended_at": at(12), "gradual_control_verified": True,
            "oracle": {"services": {"payment-service": {
                "first_violation_at": at(9), "confirmed_at": at(11),
                "buckets": {}}}}}
    from sentinelops.contracts import utc_time
    base = int(utc_time(at(0)).timestamp())
    case["oracle"]["services"]["payment-service"]["buckets"] = {
        str(base+i*60): {"availability": "VERIFIED", "breach": i >= 9,
                        "p95_latency_ms": float(100+i*50)} for i in range(12)}
    rows = []
    for i in range(5, 11):
        rows.append({"run_id": case["run_id"], "group_id": group, "window_end": at(i), "decision_at": at(i, 30),
            "risk_label": 1 if i < 9 else None, "isolation_forest_flag": i >= 9, "median_mad_flag": False,
            "xgboost_flag": i < 9, "slope_flag": False, "xgboost_score": .9, "slope_score": .1})
    return case, rows


def test_protocol_is_frozen_and_does_not_allow_final_or_post_fit_tuning(tmp_path):
    protocol = load_protocol()
    assert not protocol["activation_allowed"] and not protocol["final_access_allowed"]
    (tmp_path/"configs").mkdir()
    for changed in ({**protocol, "risk_threshold": .1}, {**protocol, "final_access_allowed": True},
                    {**protocol, "extra_field": "not-versioned"}):
        (tmp_path/"configs/local_model_evaluation.json").write_text(json.dumps(changed))
        with pytest.raises(ValueError):
            load_protocol(tmp_path)


def test_persistence_handles_minute_gaps_unknown_and_sustained_entry_exit():
    protocol = load_protocol()
    rows = [{"window_end": at(i), "decision_at": at(i, 30), "flag": flag}
            for i, flag in [(1, True), (3, True), (4, None), (5, True), (6, True),
                            (7, False), (8, False), (9, False), (10, True), (11, True)]]
    result = persistent_states(rows, "flag", protocol)
    assert [i for i, s in enumerate(result) if s["entered"]] == [4, 9]
    assert not result[2]["available"] and not result[7]["active"]
    with pytest.raises(ValueError, match="duplicate"):
        persistent_states([rows[0], rows[0]], "flag", protocol)


def test_pr_auc_and_average_precision_are_named_separately_and_unknown_is_not_zero():
    pytest.importorskip("sklearn")
    metrics = risk_curve([0, 1, 1, 0], [.1, .9, .8, .2])
    assert metrics["pr_auc_trapezoidal"] == metrics["average_precision"] == 1
    assert metrics["prevalence"] == .5
    assert risk_curve([0, 0], [.1, .2])["pr_auc_trapezoidal"] is None
    assert risk_curve([], [])["prevalence"] is None


def test_counterfactual_events_and_sustained_risk_keep_misses_and_counts():
    pytest.importorskip("sklearn")
    case, rows = case_and_predictions()
    metrics = summarize(rows, [case], "payment-service", load_protocol())
    if_events = metrics["counterfactual_detector_events"]["isolation_forest"]
    mad_events = metrics["counterfactual_detector_events"]["median_mad"]
    assert (if_events["tp"], if_events["fn"], mad_events["fn"]) == (1, 0, 1)
    assert if_events["detection_delays_seconds"] == [90]
    assert metrics["risk"]["xgboost"]["eligible_precursor_runs"] == 1
    assert metrics["risk"]["xgboost"]["precursor_recall"] == 1
    assert metrics["risk"]["slope"]["precursor_recall"] == 0
    assert metrics["risk_target_unavailable_windows"] == 2


def test_missing_predictor_frames_do_not_remove_true_precursor_from_denominator():
    pytest.importorskip("sklearn")
    case, _ = case_and_predictions()
    result = summarize([], [case], "payment-service", load_protocol())
    assert result["counterfactual_detector_events"]["isolation_forest"]["fn"] == 1
    assert result["risk"]["xgboost"]["eligible_precursor_runs"] == 1
    assert result["risk"]["xgboost"]["precursor_recall"] == 0
    assert result["risk"]["xgboost"]["pr_auc_trapezoidal"] is None


def test_group_bootstrap_is_paired_reproducible_and_rejects_window_pseudoreplication():
    pytest.importorskip("sklearn")
    cases, records = [], []
    for i in range(5):
        case, rows = case_and_predictions(f"dev-{i}")
        # A separate observed healthy run in each acquisition group supplies negatives.
        healthy = deepcopy(case)
        healthy["run_id"] += "-healthy"
        healthy["gradual_control_verified"] = False
        healthy["oracle"]["services"]["payment-service"]["first_violation_at"] = None
        cases.extend([case, healthy])
        records.extend(rows)
        records.extend({**r, "run_id": healthy["run_id"], "risk_label": 0, "isolation_forest_flag": False,
                        "xgboost_flag": False, "xgboost_score": .05} for r in rows)
    protocol = {**load_protocol(), "bootstrap_replicates": 16}  # Pure math fixture only; public loader rejects changes.
    result = paired_group_intervals(records, cases, "payment-service", protocol)
    assert result == paired_group_intervals(records, cases, "payment-service", protocol)
    assert result["groups"] == 5 and result["replicates"] == 16
    assert result["intervals"]["if_minus_mad_event_f1"]["lower"] == 1
    assert result["intervals"]["xgb_minus_slope_average_precision"]["valid_replicates"] == 16
    for row in records: row["group_id"] = "same-group"
    for case in cases: case["group_id"] = "same-group"
    with pytest.raises(DatasetNotReady, match="five whole groups"):
        paired_group_intervals(records, cases, "payment-service", protocol)


@pytest.mark.parametrize("forged_ready", [False, True])
def test_unadmitted_corpus_cannot_deserialize_models_or_create_output(tmp_path, monkeypatch, forged_ready):
    store = ArtifactStore(tmp_path/"source")
    report = {"dataset_version": "local-http-admission/v1", "training_ready": forged_ready,
              "created_at": "2026-11-01T00:00:00Z"}
    reference = store.write("manifests", report)
    def forbidden(*args, **kwargs):
        pytest.fail("model bytes must not be read before original-source admission")
    monkeypatch.setitem(sys.modules, "sentinelops.ml.models", SimpleNamespace(load_bundle=forbidden))
    output = tmp_path/"evaluation"
    with pytest.raises(DatasetNotReady):
        evaluate_local_models(store, reference, tmp_path/"missing-models",
                              training_manifest_sha256="0"*64, output=output)
    assert not output.exists()


@pytest.mark.parametrize("labels,scores", [([0, 1], [.1]), ([0, True], [.1, .9]),
                                         ([0, 2], [.1, .9]), ([0, 1], [.1, float("nan")])])
def test_invalid_risk_curve_inputs_never_produce_a_quality_score(labels, scores):
    pytest.importorskip("sklearn")
    with pytest.raises(ValueError):
        risk_curve(labels, scores)


def test_persistence_rejects_backward_decision_time_without_backdating():
    rows = [{"window_end": at(1), "decision_at": at(3), "flag": True},
            {"window_end": at(2), "decision_at": at(2, 30), "flag": True}]
    with pytest.raises(ValueError, match="clocks"):
        persistent_states(rows, "flag", load_protocol())


def test_evaluation_requires_freeze_even_after_reader_verifies_source(tmp_path, monkeypatch):
    from sentinelops.ml import local_data
    store = ArtifactStore(tmp_path/"source")
    report = {"dataset_version": "local-http-admission/v1", "training_ready": True}
    ref = store.write("manifests", report)
    # Test double isolates the freeze boundary; this is not a source certificate.
    monkeypatch.setattr(local_data, "admitted_local_evaluation_rows", lambda *_: ([], []))
    with pytest.raises(DatasetNotReady, match="frozen admission"):
        evaluate_local_models(store, ref, tmp_path/"models", training_manifest_sha256="0"*64,
                              output=tmp_path/"evaluation")
    assert not (tmp_path/"evaluation").exists()

def test_constant_risk_scores_do_not_beat_prevalence_under_primary_ap():
    pytest.importorskip("sklearn")
    result = risk_curve([0, 1], [.5, .5])
    assert result["average_precision"] == result["prevalence"] == .5
    assert result["pr_auc_trapezoidal"] == .75


def test_numeric_prediction_uses_only_features_and_preserves_unavailable_clock_rules():
    np = pytest.importorskip("numpy")
    pytest.importorskip("xgboost")
    from sentinelops.ml.models import fit_detector, fit_risk
    from sentinelops.ml.local_evaluation import predict_windows
    from sentinelops.telemetry.core import FEATURE_KEYS
    rng = np.random.default_rng(42)
    train, cal = rng.normal(size=(300, 5)), rng.normal(size=(100, 5))
    detector = fit_detector(train, cal, FEATURE_KEYS)
    classifier = fit_risk(train, (train[:, 0] > 0).astype(int), FEATURE_KEYS)
    rows = [{"features": train[i].tolist(), "window_end": at(i+1), "prediction_cutoff": at(i+1, 30),
             "risk_label": i % 2, "healthy": i == 0, "group_id": "fixture-only"} for i in range(3)]
    rows[1]["prediction_cutoff"] = at(3, 30)  # risk expired, IF can still evaluate.
    rows[2]["prediction_cutoff"] = at(6, 1)   # both beyond their freshness scope.
    protocol = load_protocol()
    frozen = deepcopy(detector.thresholds)
    first = predict_windows(rows, detector, classifier, 0., protocol)
    changed = [{**row, "risk_label": None, "healthy": not row["healthy"], "group_id": "other-label"} for row in rows]
    second = predict_windows(changed, detector, classifier, 0., protocol)
    for a, b in zip(first, second):
        for field in ("isolation_forest_score", "median_mad_score", "xgboost_score", "slope_score"):
            assert a[field] == b[field]
    assert first[0]["xgboost_flag"] is not None
    assert first[1]["xgboost_flag"] is None and first[1]["isolation_forest_flag"] is not None
    assert first[2]["xgboost_flag"] is None and first[2]["isolation_forest_flag"] is None
    assert detector.thresholds == frozen


def test_cli_reports_blocked_without_model_load_or_output(tmp_path):
    import subprocess
    store = ArtifactStore(tmp_path/"source")
    ref = store.write("manifests", {"dataset_version": "local-http-admission/v1", "training_ready": False})
    output = tmp_path/"evaluation"
    completed = subprocess.run([sys.executable, "-m", "scripts.evaluate_local_models",
        "--store", str(store.root), "--dataset-ref", ref, "--models", str(tmp_path/"not-a-model"),
        "--training-manifest-sha256", "0"*64, "--output", str(output)],
        cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 2
    result = json.loads(completed.stdout)
    assert result["status"] == "BLOCKED" and result["models_fitted"] == 0
    assert not result["holdout_opened"] and not result["activation_allowed"] and not result["G3_accepted"]
    assert not output.exists()
