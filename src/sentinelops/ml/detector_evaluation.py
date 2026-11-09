"""ADR-015 development protocol v2: detector events from abrupt runs beside v1.

Nothing here fits, refits, recalibrates or thresholds a model. Abrupt detector
events come from a separate evaluation-only epoch whose plan and this protocol
were committed before its first registration. The local-candidate evaluator that
used these functions was retired with local-data training (ADR-016).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import json
import subprocess

from sentinelops.contracts import utc_time
from sentinelops.experiments.local_corpus import PROJECT
from sentinelops.ml.evaluation import event_metrics
from sentinelops.ml.local_evaluation import PROTOCOL_PATH, load_protocol, persistent_states
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.telemetry.core import stable_id, timestamp

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
