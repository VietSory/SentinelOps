"""ADR-015 development protocol v2: detector events from abrupt runs beside v1.

Nothing here fits, refits, recalibrates or thresholds a model. The unchanged v1
candidate gate is reused; v1 gradual results are recomputed and reported as-is.
Abrupt detector events come from a separate evaluation-only epoch whose plan and
this protocol were committed before its first registration.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

from sentinelops.contracts import utc_time
from sentinelops.experiments.local_corpus import PROJECT, is_detector_plan, load_plan
from sentinelops.ml.evaluation import event_metrics
from sentinelops.ml.local_evaluation import (PROTOCOL_PATH, _verified_candidate, load_protocol,
    persistent_states, predict_windows, summarize)
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore

PROTOCOL_V2_PATH = "configs/local_model_evaluation_v2.json"
DETECTORS = ("isolation_forest", "median_mad")


def load_protocol_v2(project=PROJECT):
    base = load_protocol(project)
    value = json.loads((project / PROTOCOL_V2_PATH).read_bytes())
    expected = {"protocol_version": "local-model-development/v2", "authorizing_adr": "ADR-015",
        "base_protocol_version": "local-model-development/v1", "base_protocol_sha256": stable_id(base),
        "detector_plan_version": "local-http-detector-events-plan/v1",
        "detector_event_source": "oracle-first-violation-in-verified-abrupt-runs",
        "precursor_interval_accounting": "separate-verified-risk-positive-windows/v1",
        "evaluation_role": "development-validation", "final_access_allowed": False, "activation_allowed": False}
    if value != expected or any(type(value[k]) is not type(v) for k, v in expected.items()):
        raise ValueError("unsupported detector-event protocol v2")
    return value, base


def _healthy_seconds(rows, oracle, excluded_anchors=frozenset()):
    """v1 rule: VERIFIED non-breach bucket per available detector window."""
    seconds = 0
    for row in rows:
        if row["window_end"] in excluded_anchors:
            continue
        bucket = oracle["buckets"].get(str(int(utc_time(row["window_end"]).timestamp()) - 60))
        if row["isolation_forest_flag"] is not None and bucket and bucket["availability"] == "VERIFIED" and bucket["breach"] is False:
            seconds += 60
    return seconds


def _events(oracle, service, protocol):
    onset = oracle["first_violation_at"]
    return [] if onset is None else [{"service": service, "onset_at": onset,
        "deadline_at": timestamp(utc_time(onset) + timedelta(seconds=protocol["event_deadline_seconds"]))}]


def detector_summary(records, cases, service, protocol, *, separate_precursor=False):
    """Detector event metrics; optional ADR-015 precursor-interval accounting.

    With ``separate_precursor`` an episode entered on a window whose verified
    v1 risk label is positive is counted as ``precursor_interval_episodes``.
    It is never a true positive, and those windows leave healthy service time.
    """
    by_run = defaultdict(list)
    for row in records:
        by_run[row["run_id"]].append(row)
    result = {}
    for name in DETECTORS:
        runs, separated = [], 0
        for case in cases:
            rows = sorted(by_run[case["run_id"]], key=lambda r: r["window_end"])
            oracle = case["oracle"]["services"][service]
            positive = frozenset(r["window_end"] for r in rows if r["risk_label"] == 1) if separate_precursor else frozenset()
            alerts = []
            for row, state in zip(rows, persistent_states(rows, name + "_flag", protocol)):
                if not state["entered"]:
                    continue
                if row["window_end"] in positive:
                    separated += 1
                    continue
                alerts.append({"service": service, "emitted_at": state["decision_at"]})
            runs.append({"run_id": case["run_id"], "group_id": case["group_id"], "events": _events(oracle, service, protocol),
                         "eligible_healthy_service_seconds": _healthy_seconds(rows, oracle, positive), "alerts": alerts})
        metrics = event_metrics(runs) if runs else None
        if metrics is not None and separate_precursor:
            metrics["precursor_interval_episodes"] = separated
        result[name] = metrics
    return result


def paired_detector_interval(records, cases, service, protocol):
    """IF minus MAD event F1 over resampled whole groups (v1 seed/replicates)."""
    import numpy as np
    groups = sorted({c["group_id"] for c in cases})
    if len(groups) < 5:
        raise DatasetNotReady("detector comparison requires at least five whole groups")
    rng = np.random.default_rng(protocol["bootstrap_seed"])
    values = []
    for _ in range(protocol["bootstrap_replicates"]):
        rows, selected = [], []
        for index, group in enumerate(rng.choice(groups, size=len(groups), replace=True)):
            copied = {c["run_id"]: str(index) + ":" + c["run_id"] for c in cases if c["group_id"] == group}
            selected.extend({**c, "run_id": copied[c["run_id"]]} for c in cases if c["group_id"] == group)
            rows.extend({**r, "run_id": copied[r["run_id"]]} for r in records if r["run_id"] in copied)
        metrics = detector_summary(rows, selected, service, protocol)
        a, b = metrics["isolation_forest"]["f1"], metrics["median_mad"]["f1"]
        if a is not None and b is not None:
            values.append(a - b)
    alpha = (1 - protocol["confidence_level"]) / 2
    return {"method": "paired-whole-acquisition-group-percentile-bootstrap", "groups": len(groups),
        "replicates": protocol["bootstrap_replicates"], "seed": protocol["bootstrap_seed"],
        "if_minus_mad_event_f1": {"valid_replicates": len(values),
            "undefined_replicates": protocol["bootstrap_replicates"] - len(values),
            "lower": float(np.quantile(values, alpha)) if values else None,
            "upper": float(np.quantile(values, 1 - alpha)) if values else None},
        "limitation": "Conditional development-group uncertainty; fresh contexts do not prove statistical IID independence."}


def require_frozen_before_capture(store, project=PROJECT):
    """Plan and protocol bytes must already exist at every registered source commit."""
    from sentinelops.experiments.local_corpus import registered_sessions
    current = {path: json.loads((project / path).read_bytes())
               for path in (PROTOCOL_V2_PATH, PROTOCOL_PATH, "configs/local_http_detector_events_plan.json")}
    sessions = registered_sessions(store)
    if not sessions:
        raise DatasetNotReady("detector-event epoch has no registered groups")
    for session in sessions:
        for path, value in current.items():
            try:
                declared = subprocess.check_output(["git", "show", session["source_commit"] + ":" + path],
                                                   cwd=project, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError as exc:
                raise DatasetNotReady("detector protocol/plan was not committed before capture") from exc
            if json.loads(declared) != value:
                raise DatasetNotReady("detector protocol/plan changed after capture registration")


def evaluate_detector_events(store, dataset_ref, model_root, *, training_manifest_sha256, detector_store,
                             detector_report_ref, output, project=PROJECT):
    from sentinelops.ml.local_data import detector_evaluation_inventory
    output = Path(output)
    protocol_v2, protocol = load_protocol_v2(project)
    plan = load_plan(Path(project) / "configs/local_http_detector_events_plan.json")
    if not is_detector_plan(plan):
        raise ValueError("detector-event evaluation requires the ADR-015 plan")
    detector_report = detector_store.read(detector_report_ref)
    abrupt_rows, abrupt_cases = detector_evaluation_inventory(detector_store, plan, detector_report, project=project)
    require_frozen_before_capture(detector_store, project)
    report, rows, cases, base, evaluation_commit, verified = _verified_candidate(
        store, dataset_ref, model_root, training_manifest_sha256=training_manifest_sha256, output=output, project=project)
    if stable_id(base) != protocol_v2["base_protocol_sha256"]:
        raise ValueError("candidate gate used a different base protocol")
    if detector_report["source_profile"] != report["source_profile"] or detector_report["feature_set_version"] != report["feature_set_version"]:
        raise ValueError("detector-event epoch differs from the candidate source/feature profile")
    services, predictions, pins = {}, [], []
    gradual_cases = [c for c in cases if c["role"] == "development-validation"]
    for item in verified:
        service = item["service"]
        args = (item["detector"], item["risk"], item["slope_threshold"], protocol)
        gradual = predict_windows([r for r in rows if r["service"] == service and r["split"] == "development-validation"], *args)
        abrupt = predict_windows([r for r in abrupt_rows if r["service"] == service], *args)
        v1 = summarize(gradual, gradual_cases, service, protocol)["counterfactual_detector_events"]
        services[service] = {
            "abrupt_detector_events": detector_summary(abrupt, abrupt_cases, service, protocol),
            "abrupt_uncertainty": paired_detector_interval(abrupt, abrupt_cases, service, protocol),
            "gradual_detector_events_v1": v1,
            "gradual_detector_events_precursor_separated": detector_summary(gradual, gradual_cases, service, protocol,
                                                                            separate_precursor=True),
            "abrupt_windows": len(abrupt), "abrupt_groups": len({r["group_id"] for r in abrupt}),
            "gradual_windows": len(gradual)}
        predictions.extend({**r, "evaluation_epoch": "abrupt"} for r in abrupt)
        pins.append({"service": service, "model_version": item["manifest"]["model_version"], "manifest_sha256": item["digest"]})
    result = {"evaluation_version": "local-model-development/v2", "authorizing_adr": "ADR-015",
        "dataset_ref": dataset_ref, "detector_report_ref": detector_report_ref, "models": pins,
        "source_profile": report["source_profile"], "feature_set_version": report["feature_set_version"],
        "training_data_policy_hash": report["training_data_policy_hash"],
        "training_manifest_sha256": training_manifest_sha256, "evaluation_source_commit": evaluation_commit,
        "protocol": protocol_v2, "base_protocol": protocol, "protocol_sha256": stable_id(protocol_v2),
        "evaluated_at": timestamp(datetime.now(timezone.utc)), "services": services,
        "activation_allowed": False, "holdout_opened": False, "G3_accepted": False,
        "limitations": ["Development only; no final holdout or production generalization claim.",
            "Abrupt events are prospective evaluation-only groups captured after this protocol was committed.",
            "Gradual results reuse previously inspected development groups; precursor-separated accounting on them is not independent evidence.",
            "Event metrics use recorded feature cutoffs with instantaneous counterfactual inference, not historical alert emission.",
            "Risk/XGBoost evidence remains the v1 gradual protocol; abrupt runs supply no precursor labels.",
            "Live inference timing, parity and all remaining gates require separate evidence."]}
    output.mkdir(parents=True, exist_ok=False)
    destination = ArtifactStore(output)
    result["predictions_ref"] = destination.write("manifests", {"table_version": "local-detector-event-predictions/v1",
                                                                "rows": predictions})
    reference = destination.write("manifests", result)
    return {"report_ref": reference, "activation_allowed": False, "holdout_opened": False, "G3_accepted": False}
