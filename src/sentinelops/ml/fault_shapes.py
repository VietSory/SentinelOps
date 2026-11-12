"""ADR-019 pre-capture gate: can the fitted models respond to the fault shapes the local epochs inject?

Layer 2 of bundle ``badf9da0`` failed for reasons a few seconds of synthetic minutes would have shown: the
Isolation Forest score levelled off below its threshold for a latency-only fault, and the risk model had no
labelled window near an absolute SLO. This gate replays the frozen recipe (healthy, abrupt delay, linear ramp)
through the real transform, persistence and event matching, with no local capture and no Alibaba evaluation
service. It rejects a design that cannot work. It is not evidence that a design does work on real traffic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sentinelops.ml.alibaba_training import BREACH_FEATURE, service_windows_fast
from sentinelops.ml.evaluation import event_metrics
from sentinelops.ml.local_evaluation import persistent_states, risk_curve
from sentinelops.telemetry.core import timestamp

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
SERVICE = "synthetic-service"


def _clock(seconds):
    return timestamp(EPOCH + timedelta(seconds=seconds))


def sequences(spec, breach_ms):
    """Deterministic per-minute latency for every (kind, base latency, noise, seed) cell of the frozen spec."""
    import numpy as np
    warm, recovery = spec["healthy_minutes_before_fault"], spec["recovery_minutes"]
    result = []
    for kind_index, kind in enumerate(("healthy", "abrupt", "gradual")):
        active = {"healthy": spec["healthy_only_minutes"] - warm - recovery, "abrupt": spec["abrupt_minutes"],
                  "gradual": spec["gradual_minutes"]}[kind]
        for base_index, base in enumerate(spec["base_latency_ms"]):
            for noise_index, sigma in enumerate(spec["noise_sigma"]):
                for seed in range(spec["seeds_per_cell"]):
                    rng = np.random.default_rng([kind_index, base_index, noise_index, seed])
                    n = warm + active + recovery
                    delay = np.zeros(n)
                    if kind == "abrupt":
                        delay[warm:warm + active] = spec["fault_delay_ms"]
                    elif kind == "gradual":
                        delay[warm:warm + active] = spec["fault_delay_ms"] * (np.arange(active) + .5) / active
                    rt = (base + delay) * np.exp(rng.normal(0, sigma, n))
                    rate = 60. * np.exp(rng.normal(0, .02, n))
                    above = rt > breach_ms
                    onset = next((m for m in range(n - 1) if above[m] and above[m + 1]), None)
                    result.append({"run_id": f"{kind}-{base:g}ms-{sigma:g}-{seed}", "kind": kind, "rt": rt, "rate": rate,
                                   "fault_start": None if kind == "healthy" else warm,
                                   "fault_end": None if kind == "healthy" else warm + active, "onset": onset})
    return result


def _windows(sequence, config, spec):
    import pandas as pd
    frame = pd.DataFrame({"rt": sequence["rt"], "rate": sequence["rate"]}, index=range(len(sequence["rt"])))
    rows = []
    for row in service_windows_fast(frame, config):
        end = 60 * (row["minute"] + 1)
        rows.append({"minute": row["minute"], "values": row["features"], "window_end": _clock(end),
                     "decision_at": _clock(end + spec["decision_lag_seconds"]), "end_seconds": end})
    return rows


def fault_shape_gate(detector, risk, config, protocol, risk_threshold):
    """Score the frozen synthetic recipe. Returns every measured number and one boolean ``passed``."""
    import numpy as np
    spec = config["fault_shape_gate"]
    if list(config["features"])[-1] != BREACH_FEATURE:
        raise ValueError("fault-shape gate needs the breach-relative feature set")
    slope_index = config["features"].index("rt_slope_rel_per_min")
    deadline = protocol["event_deadline_seconds"]
    runs = {"isolation_forest": [], "median_mad": []}
    margins, labels, scores, control = [], [], [], []
    eligible = qualified = false_warnings = 0
    leads = []
    for sequence in sequences(spec, config["serving"]["breach_latency_ms"]):
        rows = _windows(sequence, config, spec)
        if not rows:
            raise ValueError("fault-shape sequence produced no window")
        values = [row["values"] for row in rows]
        detected = detector.predict(values)
        risks = risk.predict_proba(values)[:, 1]
        onset = None if sequence["onset"] is None else 60 * sequence["onset"]
        start = None if sequence["fault_start"] is None else 60 * sequence["fault_start"]
        if sequence["kind"] != "gradual":
            events = [] if onset is None else [{"service": SERVICE, "onset_at": _clock(onset),
                                                "deadline_at": _clock(onset + deadline)}]
            healthy = 60 * sum(1 for row in rows if start is None or row["end_seconds"] <= start)
            for name in runs:
                records = [{**row, "flag": bool(detected[name]["alert"][i])} for i, row in enumerate(rows)]
                states = persistent_states(records, "flag", protocol)
                runs[name].append({"run_id": sequence["run_id"], "events": events,
                                   "eligible_healthy_service_seconds": healthy,
                                   "alerts": [{"service": SERVICE, "emitted_at": s["decision_at"]} for s in states if s["entered"]]})
            if sequence["kind"] == "abrupt":
                inside = [i for i, row in enumerate(rows)
                          if row["minute"] - config["window_minutes"] + 1 >= sequence["fault_start"]
                          and row["minute"] < sequence["fault_end"]]
                margins.append(float(np.min(detected["isolation_forest"]["raw_score"][inside])
                                     - detector.thresholds["isolation_forest"]))
        records = [{**row, "flag": bool(risks[i] >= risk_threshold)} for i, row in enumerate(rows)]
        states = persistent_states(records, "flag", protocol)
        false_warnings += sum(1 for row, state in zip(rows, states)
                              if state["entered"] and (start is None or row["end_seconds"] <= start))
        if sequence["kind"] != "gradual":
            continue
        if onset is None:
            raise ValueError("gradual fault-shape sequence never breaches")
        eligible += 1
        tail = 60 * config["label"]["confirmation_tail_minutes"]
        horizon = config["label"]["horizon_seconds"]
        warned = []
        for i, (row, state) in enumerate(zip(rows, states)):
            ahead = onset - row["end_seconds"]
            label = 1 if 0 <= ahead < horizon else 0 if ahead > horizon + tail else None
            if label is None:
                continue
            labels.append(label); scores.append(float(risks[i])); control.append(max(0., row["values"][slope_index]))
            decision = row["end_seconds"] + spec["decision_lag_seconds"]
            if label == 1 and state["active"] and decision < onset:
                warned.append(onset - decision)
        if warned:
            leads.append(max(warned))
            qualified += max(warned) >= 60
    metrics = {name: event_metrics(items) for name, items in runs.items()}
    for item in metrics.values():
        item.pop("per_run", None)
    forest, mad = metrics["isolation_forest"], metrics["median_mad"]
    curve, baseline = risk_curve(labels, scores), risk_curve(labels, control)
    required = spec["pass"]
    recall = qualified / eligible if eligible else None
    checks = {
        "detector_event_recall": forest["recall"] is not None and forest["recall"] >= required["detector_event_recall"],
        "detector_false_alerts": forest["fp"] <= required["detector_false_alerts"],
        "detector_f1_not_below_mad": forest["f1"] is not None and (mad["f1"] is None or forest["f1"] >= mad["f1"]),
        "detector_saturation_margin": bool(margins) and min(margins) >= required["detector_min_saturation_margin"],
        "risk_average_precision_above_slope_control": curve["average_precision"] is not None
            and baseline["average_precision"] is not None and curve["average_precision"] > baseline["average_precision"],
        "risk_precursor_recall_lead_60s": recall is not None and recall >= required["risk_precursor_recall_lead_60s"],
        "risk_false_warnings": false_warnings <= required["risk_false_warnings"],
    }
    return {"gate_version": spec["gate_version"], "sequences": len(sequences(spec, config["serving"]["breach_latency_ms"])), "detector_events": metrics,
            "detector_min_saturation_margin": min(margins) if margins else None,
            "risk": {"model": curve, "slope_control": baseline, "threshold": risk_threshold,
                     "eligible_precursor_runs": eligible, "lead_qualified_precursor_runs": qualified,
                     "precursor_recall_lead_ge_60s": recall, "lead_seconds_min": min(leads) if leads else None,
                     "false_warnings": false_warnings},
            "checks": checks, "passed": all(checks.values()),
            "limitations": ["Synthetic minutes with log-normal noise; no local capture and no Alibaba evaluation service.",
                            "A pass shows the design can respond to these shapes, not that it performs on real traffic."]}
