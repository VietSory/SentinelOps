"""Source-gated development comparison; never final evaluation or activation."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import json

from sentinelops.contracts import utc_time
from sentinelops.experiments.local_corpus import PROJECT
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.telemetry.core import FEATURE_KEYS, FEATURE_SET, timestamp

PROTOCOL_PATH = "configs/local_model_evaluation.json"


def load_protocol(project=PROJECT):
    value = json.loads((project / PROTOCOL_PATH).read_bytes())
    fixed = {"protocol_version": "local-model-development/v1", "source_profile": "prometheus-increase-60s-5s/v1",
             "feature_set_version": FEATURE_SET, "evaluation_role": "development-validation",
             "clock_mode": "recorded-feature-cutoff-instantaneous-counterfactual",
             "pr_auc_method": "trapezoidal-precision-recall", "primary_risk_comparison": "average_precision", "final_access_allowed": False, "activation_allowed": False}
    for key, expected in fixed.items():
        if value.get(key) != expected or type(value[key]) is not type(expected):
            raise ValueError("unsupported development evaluation protocol")
    numbers = {"false_alert_budget": .01, "entry_windows": 2, "exit_windows": 3, "step_seconds": 60,
               "event_deadline_seconds": 300, "risk_horizon_seconds": 300, "risk_threshold": .8,
               "bootstrap_replicates": 1000, "bootstrap_seed": 42, "confidence_level": .95}
    if set(value) != set(fixed) | set(numbers) | {"slope_feature"} or value["slope_feature"] != FEATURE_KEYS[2]:
        raise ValueError("unsupported development protocol fields")
    if any(type(value[k]) is not type(v) or value[k] != v for k, v in numbers.items()):
        raise ValueError("protocol v1 parameters are frozen; use a new version before fitting")
    return value


def persistent_states(records, field, protocol):
    """Only past flags/closed minute IDs enter persistence, never outcome labels."""
    active = False
    entry = exit_count = 0
    previous = previous_decision = None
    result = []
    for row in sorted(records, key=lambda r: utc_time(r["window_end"])):
        anchor, decision = utc_time(row["window_end"]), utc_time(row["decision_at"])
        if (decision < anchor or previous is not None and anchor <= previous
            or previous_decision is not None and decision < previous_decision):
            raise ValueError("duplicate/inverted feature clocks")
        if previous is None or (anchor - previous).total_seconds() != protocol["step_seconds"]:
            entry = exit_count = 0
        flag = row[field]
        if flag is None:
            entry = exit_count = 0
        elif type(flag) is not bool:
            raise ValueError("persistence needs boolean or unavailable flags")
        elif flag:
            entry += 1
            exit_count = 0
        else:
            exit_count += 1
            entry = 0
        emitted = False
        if flag is True and not active and entry >= protocol["entry_windows"]:
            active, emitted = True, True
        elif flag is False and exit_count >= protocol["exit_windows"]:
            active = False
        result.append({"active": active, "available": flag is not None, "entered": emitted,
                       "decision_at": row["decision_at"]})
        previous, previous_decision = anchor, decision
    return result


def risk_curve(labels, scores):
    from sklearn.metrics import auc, average_precision_score, precision_recall_curve
    import math
    if (len(labels) != len(scores) or any(type(v) is not int or v not in (0, 1) for v in labels)
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in scores)):
        raise ValueError("risk curves require aligned verified labels and finite scores")
    if not labels or set(labels) != {0, 1}:
        return {"windows": len(labels), "positives": sum(labels), "prevalence": None if not labels else sum(labels)/len(labels),
                "pr_auc_trapezoidal": None, "average_precision": None, "missing_reason": "both-target-classes-required"}
    precision, recall, _ = precision_recall_curve(labels, scores)
    return {"windows": len(labels), "positives": sum(labels), "prevalence": sum(labels)/len(labels),
            "pr_auc_trapezoidal": float(auc(recall, precision)), "average_precision": float(average_precision_score(labels, scores)),
            "missing_reason": None}


def _precursor_start(case, service, horizon):
    """Eligibility is derived from the independent oracle, even if no model frame exists."""
    evidence = case["oracle"]["services"][service]
    if not case["gradual_control_verified"] or not evidence["first_violation_at"]:
        return None
    onset = utc_time(evidence["first_violation_at"])
    buckets = evidence["buckets"]
    for offset in range(horizon, 0, -60):
        anchor = onset - timedelta(seconds=offset)
        history = [buckets.get(str(int(anchor.timestamp()) - 60*i)) for i in range(5, 0, -1)]
        if all(b and b["availability"] == "VERIFIED" for b in history):
            prefix = [b["p95_latency_ms"] for b in history[-3:]]
            if all(a < b for a, b in zip(prefix, prefix[1:])):
                return timestamp(anchor)
    return None


def summarize(records, cases, service, protocol):
    """Counterfactual decision clocks stay explicitly separate from real alert clocks."""
    from sentinelops.ml.evaluation import event_metrics
    by_run = defaultdict(list)
    for row in records:
        by_run[row["run_id"]].append(row)
    detectors = {name: [] for name in ("isolation_forest", "median_mad")}
    precursor = {name: [] for name in ("xgboost", "slope")}
    for case in cases:
        rows = sorted(by_run[case["run_id"]], key=lambda r: r["window_end"])
        oracle = case["oracle"]["services"][service]
        onset = oracle["first_violation_at"]
        events = [] if onset is None else [{"service": service, "onset_at": onset,
            "deadline_at": timestamp(utc_time(onset) + timedelta(seconds=protocol["event_deadline_seconds"]))}]
        healthy_seconds = 0
        for row in rows:
            bucket = oracle["buckets"].get(str(int(utc_time(row["window_end"]).timestamp())-60))
            if row["isolation_forest_flag"] is not None and bucket and bucket["availability"] == "VERIFIED" and bucket["breach"] is False:
                healthy_seconds += 60
        for name in detectors:
            states = persistent_states(rows, name + "_flag", protocol)
            detectors[name].append({"run_id": case["run_id"], "group_id": case["group_id"], "events": events,
                "eligible_healthy_service_seconds": healthy_seconds, "alerts": [
                    {"service": service, "emitted_at": s["decision_at"]} for s in states if s["entered"]]})
        start = _precursor_start(case, service, protocol["risk_horizon_seconds"])
        for name in precursor:
            states = persistent_states(rows, name + "_flag", protocol)
            points = [s["decision_at"] for row, s in zip(rows, states)
                      if row["risk_label"] == 1 and s["active"] and s["available"]
                      and start is not None and utc_time(start) <= utc_time(s["decision_at"]) < utc_time(onset)]
            precursor[name].append({"run_id": case["run_id"], "eligible": start is not None,
                "detected": bool(points), "counterfactual_lead_seconds":
                (utc_time(onset)-utc_time(points[0])).total_seconds() if points else None})
    censored = sum(bool(c["oracle"]["services"][service]["first_violation_at"]) and
        utc_time(c["ended_at"]) < utc_time(c["oracle"]["services"][service]["first_violation_at"]) +
        timedelta(seconds=protocol["event_deadline_seconds"]) for c in cases)
    labelled = [r for r in records if type(r["risk_label"]) is int and r["risk_label"] in (0, 1)]
    eligible = [r for r in labelled if r["xgboost_flag"] is not None and r["slope_flag"] is not None]
    result = {"event_followup_censored_runs": censored, "counterfactual_detector_events": {name: event_metrics(rows) for name, rows in detectors.items()},
              "risk_target_unavailable_windows": sum(r["risk_label"] is None for r in records),
              "risk_labelled_prediction_unavailable_windows": len(labelled) - len(eligible), "risk": {}}
    for name in precursor:
        rows = precursor[name]
        count = sum(r["eligible"] for r in rows)
        detected = sum(r["detected"] for r in rows)
        qualified = sum(r["eligible"] and r["counterfactual_lead_seconds"] is not None
                        and r["counterfactual_lead_seconds"] >= 60 for r in rows)
        result["risk"][name] = {**risk_curve([r["risk_label"] for r in eligible], [r[name + "_score"] for r in eligible]),
            "eligible_precursor_runs": count, "detected_precursor_runs": detected,
            "precursor_recall": detected/count if count else None,
            "qualifying_lead_seconds": 60, "lead_qualified_precursor_runs": qualified,
            "precursor_recall_lead_ge_60s": qualified/count if count else None,
            "late_precursor_runs": detected-qualified, "missed_precursor_runs": count-detected,
            "per_run": rows}
    return result


def paired_group_intervals(records, cases, service, protocol):
    import numpy as np
    groups = sorted({c["group_id"] for c in cases})
    if len(groups) < 5:
        raise DatasetNotReady("development comparison requires at least five whole groups")
    rng = np.random.default_rng(protocol["bootstrap_seed"])
    samples = {"if_minus_mad_event_f1": [], "xgb_minus_slope_average_precision": []}
    for _ in range(protocol["bootstrap_replicates"]):
        selected_rows, selected_cases = [], []
        for copy_index, group in enumerate(rng.choice(groups, size=len(groups), replace=True)):
            copied = {c["run_id"]: str(copy_index) + ":" + c["run_id"] for c in cases if c["group_id"] == group}
            selected_cases.extend({**c, "run_id": copied[c["run_id"]]} for c in cases if c["group_id"] == group)
            selected_rows.extend({**r, "run_id": copied[r["run_id"]]} for r in records if r["run_id"] in copied)
        metrics = summarize(selected_rows, selected_cases, service, protocol)
        detectors, risk = metrics["counterfactual_detector_events"], metrics["risk"]
        for key, a, b in (("if_minus_mad_event_f1", detectors["isolation_forest"]["f1"], detectors["median_mad"]["f1"]),
                          ("xgb_minus_slope_average_precision", risk["xgboost"]["average_precision"], risk["slope"]["average_precision"])):
            if a is not None and b is not None:
                samples[key].append(a-b)
    alpha = (1-protocol["confidence_level"])/2
    return {"method": "paired-whole-acquisition-group-percentile-bootstrap", "groups": len(groups),
        "replicates": protocol["bootstrap_replicates"], "seed": protocol["bootstrap_seed"],
        "intervals": {key: {"valid_replicates": len(values),
            "undefined_replicates": protocol["bootstrap_replicates"]-len(values),
            "lower": float(np.quantile(values, alpha)) if values else None,
            "upper": float(np.quantile(values, 1-alpha)) if values else None} for key, values in samples.items()},
        "limitation": "Conditional development-group uncertainty; fresh contexts do not prove statistical IID independence."}


def predict_windows(development, detector, risk, slope_threshold, protocol):
    """Numerical replay helper only; this function never admits or activates a source."""
    import numpy as np
    slope_index = list(FEATURE_KEYS).index(protocol["slope_feature"])
    # All predictions are computed from vectors before joining evaluation outcomes.
    values = [r["features"] for r in development]
    detected = detector.predict(values)
    risks = risk.predict_proba(values)[:, 1]
    if not np.isfinite(risks).all() or not ((0 <= risks) & (risks <= 1)).all():
        raise ValueError("nonfinite/out-of-range risk prediction")
    records = []
    for index, row in enumerate(development):
        anchor, cutoff = utc_time(row["window_end"]), utc_time(row["prediction_cutoff"])
        risk_available = anchor <= cutoff < anchor + timedelta(seconds=60)
        detector_available = anchor <= cutoff <= anchor + timedelta(seconds=180)
        record = {**row, "decision_at": row["prediction_cutoff"]}
        for name in ("isolation_forest", "median_mad"):
            record[name + "_score"] = float(detected[name]["score"][index])
            record[name + "_flag"] = bool(detected[name]["alert"][index]) if detector_available else None
        record.update(xgboost_score=float(risks[index]), slope_score=max(0., values[index][slope_index]),
            xgboost_flag=bool(risks[index] >= protocol["risk_threshold"]) if risk_available else None,
            slope_flag=bool(values[index][slope_index] > slope_threshold) if risk_available else None)
        records.append(record)
    return records
