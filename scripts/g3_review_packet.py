"""Render the owner's G3 review packet from pinned v1/v2 reports; decides nothing.

Inputs are the candidate handoff (v1 development report and bundles) and the
ADR-015 v2 report. The packet adds one source diagnostic: each route's training healthy
p95 centre/IQR against development healthy spread. Isolation Forest is
invariant to per-feature affine scaling, so a narrow *training range* (not the
scaler) is what makes ordinary host drift look anomalous. The output
is Markdown for a human reviewer; G3 stays an owner decision.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sentinelops.ml.local_data import _inside
from sentinelops.ml.release import _audit, validate_v2_report
from sentinelops.telemetry.core import FEATURE_KEYS
from sentinelops.telemetry.store import ArtifactStore


def _fmt(value, digits=3):
    return "—" if value is None else (f"{value:.{digits}f}" if isinstance(value, float) else str(value))


def _events(metrics):
    if metrics is None:
        return "—"
    return (f"TP {metrics['tp']} / FN {metrics['fn']} / FP {metrics['fp']}; F1 {_fmt(metrics['f1'])}; "
            f"false/healthy-h {_fmt(metrics['false_incidents_per_healthy_service_hour'], 2)}")


def scaler_diagnostic(candidate, index, v1):
    import numpy as np
    from sentinelops.ml.models import load_bundle
    rows = _audit(candidate, v1["predictions_ref"])["rows"]
    result = {}
    for model in index["models"]:
        service = model["service"]
        _, detector, _ = load_bundle(_inside(candidate, "runtime/" + model["bundle_directory"]),
                                     expected_manifest_sha256=model["manifest_sha256"])
        certified = [r for r in rows if r["service"] == service and r["healthy"] is True]
        healthy = np.array([r["features"] for r in certified])
        flags = [r["isolation_forest_flag"] for r in certified]
        index_p95 = list(FEATURE_KEYS).index("p95_latency_ms_mean_5m")
        result[service] = {"train_center_p95": float(detector.scaler.center_[index_p95]),
            "train_iqr_p95": float(detector.scaler.scale_[index_p95]),
            "dev_healthy_median_p95": float(np.median(healthy[:, index_p95])) if len(healthy) else None,
            "dev_healthy_iqr_p95": float(np.subtract(*np.quantile(healthy[:, index_p95], [.75, .25]))) if len(healthy) else None,
            "dev_healthy_flag_rate": (sum(f is True for f in flags) / len(flags)) if flags else None}
    return result


def render(candidate, v2, report_ref):
    candidate = Path(candidate).resolve(strict=True)
    index = json.loads(_inside(candidate, "handoff.json").read_bytes())
    v1 = _audit(candidate, index["evaluation_ref"])
    validate_v2_report(v2, v1, report_ref)
    diagnostic = scaler_diagnostic(candidate, index, v1)
    lines = [f"# G3 review packet — candidate `{index['candidate_id'][:12]}…`", "",
        "Owner decision required. This packet accepts nothing; `G3_accepted=false` in every source report.", "",
        f"- candidate ID: `{index['candidate_id']}`",
        f"- v1 report: `{index['evaluation_ref']}` (`local-model-development/v1`)",
        f"- v2 report: `{report_ref}` (`{v2['protocol']['protocol_version']}`), detector epoch `{v2['detector_report_ref']}`",
        f"- dataset: `{v1['dataset_ref']}`; source profile `{v1['source_profile']}`", "",
        "## Per-route evidence", "",
        "| Service | XGBoost AP / precursor recall ≥60 s (v1 gradual) | IF abrupt events (v2) | MAD abrupt events (v2) | IF−MAD F1 95% CI (abrupt) | IF gradual v1 | IF gradual precursor-separated |",
        "| --- | --- | --- | --- | --- | --- | --- |"]
    for service in sorted(v2["services"]):
        risk = v1["services"][service]["metrics"]["risk"]["xgboost"]
        item = v2["services"][service]
        interval = item["abrupt_uncertainty"]["if_minus_mad_event_f1"]
        separated = item["gradual_detector_events_precursor_separated"]["isolation_forest"]
        lines.append(f"| {service} | {_fmt(risk['average_precision'])} / {_fmt(risk['precursor_recall_lead_ge_60s'])} | "
            f"{_events(item['abrupt_detector_events']['isolation_forest'])} | {_events(item['abrupt_detector_events']['median_mad'])} | "
            f"[{_fmt(interval['lower'])}, {_fmt(interval['upper'])}] ({interval['valid_replicates']} valid) | "
            f"{_events(item['gradual_detector_events_v1']['isolation_forest'])} | "
            f"{_events(separated)}; precursor episodes {separated.get('precursor_interval_episodes')} |")
    lines += ["", "## Source diagnostic: training range versus certified healthy development windows", "",
        "| Service | train p95 centre / IQR (ms) | dev healthy p95 median / IQR (ms) | dev healthy IF flag rate |",
        "| --- | --- | --- | --- |"]
    for service, d in sorted(diagnostic.items()):
        lines.append(f"| {service} | {_fmt(d['train_center_p95'], 2)} / {_fmt(d['train_iqr_p95'], 3)} | "
                     f"{_fmt(d['dev_healthy_median_p95'], 2)} / {_fmt(d['dev_healthy_iqr_p95'], 3)} | {_fmt(d['dev_healthy_flag_rate'], 2)} |")
    lines += ["", "## Limitations carried from the reports", ""] + [f"- {x}" for x in v2["limitations"]] + [
        "- Training healthy p95 occupies a very narrow range; IF isolates any value outside it regardless of scaling. More independent healthy training variance is required, not a preprocessing change.",
        "", "## Decision options for the owner", "",
        "1. Accept G3 for this candidate with the stated limitations, then build the release (`scripts.build_model_release`) after B's parity record.",
        "2. Reject G3 and require a successor candidate, frozen before it sees any further development data.",
        "3. Defer pending additional independent evidence."]
    return "\n".join(lines) + "\n", diagnostic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--v2-store", type=Path, required=True, help="Output store from evaluate_detector_events")
    parser.add_argument("--v2-report-ref", required=True, help="Report reference emitted by evaluate_detector_events")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = ArtifactStore(args.v2_store).read(args.v2_report_ref)
    text, _ = render(args.candidate, report, args.v2_report_ref)
    with args.output.open("x") as stream:
        stream.write(text)
    print(str(args.output))


if __name__ == "__main__":
    main()
