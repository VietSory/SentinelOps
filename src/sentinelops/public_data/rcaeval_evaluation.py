"""Execute telemetry-only diagnostics before joining any evaluator labels."""
from __future__ import annotations

import json
from pathlib import Path

from sentinelops.incidents.native_rca import evaluate_native
from sentinelops.public_data.rcaeval_native import NativeSourceError, adapt_native_case, digest, load_native_inputs
from sentinelops.public_data.rcaeval_partitions import INSPECTED, REVISION, canonical, read_index


def evaluate_manifest(manifest_path: Path, profile: dict, policy: dict, *, source_commit: str) -> dict:
    manifest, inputs = load_native_inputs(manifest_path, profile)
    if set(inputs) != set(INSPECTED):
        raise ValueError("measured native evaluation must account for all three predeclared cases")
    business = {}
    quarantined = {}
    for case, tables in sorted(inputs.items()):
        try:
            scope = adapt_native_case(tables, profile)
            business[case] = {"observations": scope, "diagnosis": evaluate_native(scope, policy)}
        except NativeSourceError as exc:
            # Adapter reasons are fixed strings; arbitrary parser exceptions fail the run.
            quarantined[case] = str(exc)
    frozen = canonical(business)
    # The label reader cannot participate in selecting windows, candidates or scoring.
    rows, _ = read_index(manifest_path)
    labels = {row["case"]: row for row in rows if row["case"] in inputs}
    if set(labels) != set(inputs):
        raise ValueError("evaluator labels do not account for every selected case")
    results, qualified, correct = [], 0, 0
    for case in sorted(inputs):
        row = labels[case]
        detail = {"case": case, "role": "development", "ground_truth_service": row["root_cause_service"],
                  "label_clock_in_capture": row["time_start"] <= row["inject_time"] <= row["time_end"]}
        if case in quarantined:
            detail.update({"status": "QUARANTINED", "reason": quarantined[case]})
        else:
            value = business[case]
            diagnosis = value["diagnosis"]
            prediction = diagnosis["qualified_root"]
            qualified += prediction is not None
            correct += prediction is not None and prediction == row["root_cause_service"]
            detail.update({"status": "EVALUATED", **value,
                           "ground_truth_in_native_entities": row["root_cause_service"] in value["observations"]["entities"],
                           "qualified_correct": prediction == row["root_cause_service"] if prediction is not None else None})
        results.append(detail)
    if frozen != canonical(business):
        raise ValueError("business result changed during evaluator label join")
    evaluated = len(business)
    abstained = sum(value["diagnosis"]["abstained"] for value in business.values())
    return {"evaluation_version": "rcaeval-native-development/v1", "source_commit": source_commit,
            "source_revision": REVISION, "source_manifest_sha256": manifest_path.stem,
            "profile_sha256": digest(profile), "policy_sha256": digest(policy),
            "business_results_sha256": digest(json.loads(frozen)),
            "status": "PASS" if evaluated == len(inputs) else "INCOMPLETE",
            "denominators": {"selected": len(inputs), "evaluated": evaluated, "quarantined": len(quarantined),
                             "abstained": abstained, "qualified": qualified, "qualified_correct": correct},
            "conditional_qualified_accuracy": correct / qualified if qualified else None,
            "abstention_rate": abstained / evaluated if evaluated else None,
            "cases": results, "holdout_opened": False, "ml_fitting_allowed": False,
            "independent_sessions_established": False, "full_G4_accepted": False, "actions_allowed": False,
            "limitations": ["Three previously inspected OB development cases; no independent heldout quality estimate.",
                            "PASS describes source-to-policy execution, not RCA accuracy or operational readiness.",
                            "Event-time windows have no verified source receipt clocks or SLO opening/onset evidence.",
                            "Native span work fractions and lexical exception markers are research-only support.",
                            "Recorded parent links are observations, not a complete topology or causal graph.",
                            "No healthy inference, HTTP RED mapping, approved anomaly model or precursor labels."]}
