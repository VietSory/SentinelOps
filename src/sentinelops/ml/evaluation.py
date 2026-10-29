"""Run-level event metrics. Truth stays in this evaluator, outside inference."""
from __future__ import annotations

import math
from statistics import mean

from sentinelops.contracts import utc_time


def event_metrics(runs: list[dict]):
    if not runs or len({r["run_id"] for r in runs}) != len(runs):
        raise ValueError("evaluation needs unique run denominators")
    totals = {"tp": 0, "fp": 0, "fn": 0, "tn_runs": 0}
    delays, leads, details = [], [], []
    risk_tp, risk_fp, risk_fn, exposure = 0, 0, 0, 0.
    for run in runs:
        truth = sorted(run["events"], key=lambda e: utc_time(e["onset_at"]))
        if any(utc_time(e["deadline_at"]) < utc_time(e["onset_at"]) for e in truth):
            raise ValueError("event matching interval is inverted")
        matched = set()
        seconds = run.get("eligible_healthy_service_seconds", 0.)
        if type(seconds) not in (float, int) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("healthy exposure must be measured eligible service seconds")
        exposure += seconds
        counts = {"tp": 0, "fp": 0, "fn": 0}
        for alert in sorted(run["alerts"], key=lambda a: utc_time(a["emitted_at"])):
            emission = utc_time(alert["emitted_at"])
            candidates = [(i, event) for i, event in enumerate(truth) if i not in matched
                and event["service"] == alert["service"] and utc_time(event["onset_at"]) <= emission <= utc_time(event["deadline_at"])]
            if not candidates:
                counts["fp"] += 1
                continue
            index, event = candidates[0]
            matched.add(index); counts["tp"] += 1
            delays.append((emission - utc_time(event["onset_at"])).total_seconds())
        counts["fn"] = len(truth) - len(matched)
        for key in counts:
            totals[key] += counts[key]
        totals["tn_runs"] += not truth and not run["alerts"]
        eligible_risk = [e for e in truth if e.get("gradual_precursor_verified") is True and e.get("precursor_start_at")]
        warned = set()
        for warning in sorted(run.get("risk_warnings", []), key=lambda a: utc_time(a["emitted_at"])):
            emission = utc_time(warning["emitted_at"])
            event = next(((i, e) for i, e in enumerate(eligible_risk) if i not in warned and e["service"] == warning["service"]
                          and utc_time(e["precursor_start_at"]) <= emission < utc_time(e["onset_at"])), None)
            if event:
                i, e = event
                warned.add(i); risk_tp += 1
                leads.append((utc_time(e["onset_at"]) - emission).total_seconds())
            else:
                risk_fp += 1
        risk_fn += len(eligible_risk) - len(warned)
        details.append({"run_id": run["run_id"], "service": run.get("service"), "scenario": run.get("scenario"), **counts})
    tp, fp, fn = (totals[k] for k in ("tp", "fp", "fn"))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return {"evaluation_version": "independent-run-events/v1", "runs": len(runs), **totals,
            "precision": precision, "recall": recall, "f1": f1, "detection_delays_seconds": delays,
            "mean_detection_delay_seconds": mean(delays) if delays else None, "measured_leads_seconds": leads,
            "eligible_healthy_service_hours": exposure / 3600,
            "false_incidents_per_healthy_service_hour": fp / (exposure / 3600) if exposure else None,
            "risk_tp": risk_tp, "risk_fp": risk_fp, "risk_fn": risk_fn,
            "risk_precision": risk_tp / (risk_tp + risk_fp) if risk_tp + risk_fp else None,
            "risk_recall": risk_tp / (risk_tp + risk_fn) if risk_tp + risk_fn else None,
            "per_run": details, "uncertainty": None,
            "uncertainty_reason": "Session independence and bootstrap protocol require separate evidence; windows are not independent runs.",
            "point_adjustment": False}
