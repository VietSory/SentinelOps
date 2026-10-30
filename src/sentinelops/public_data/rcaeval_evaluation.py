"""Execute telemetry-only diagnostics before joining any evaluator labels."""
from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import re
from sentinelops.incidents.rca import COMPONENTS, rank_native_support, unavailable
from sentinelops.telemetry.core import stable_id, timestamp
from pathlib import Path

from sentinelops.incidents.native_rca import evaluate_native
from sentinelops.public_data.rcaeval_native import NativeSourceError, adapt_native_case, digest, load_native_inputs
from sentinelops.public_data.rcaeval_partitions import INSPECTED, REVISION, canonical, read_index


def evaluate_manifest(manifest_path: Path, profile: dict, policy: dict, *, source_commit: str,
                      include_ablations: bool = False) -> dict:
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
    # Supplemental comparisons reuse the same admitted source and fixed interval.
    # Compute them before any ground-truth metadata can enter this evaluator.
    ablations = {case: diagnose_ablations(value["observations"], policy)
                 for case, value in business.items()} if include_ablations else {}
    frozen = canonical(business)
    frozen_ablations = canonical(ablations)
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
    if frozen != canonical(business) or frozen_ablations != canonical(ablations):
        raise ValueError("business result changed during evaluator label join")
    evaluated = len(business)
    abstained = sum(value["diagnosis"]["abstained"] for value in business.values())
    report = {"evaluation_version": "rcaeval-native-development/v1", "source_commit": source_commit,
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
    if include_ablations:
        report["ablations"] = ablations
        report["metrics"] = summarize_ablations(ablations, labels)
    return report


def diagnose_ablations(scope: dict, policy: dict) -> dict:
    """Remove actually observed support; never introduce unavailable modalities."""
    results = {"native-traces-and-logs": evaluate_native(scope, policy)}
    for name, field in (("without-native-logs", "log_support"),
                        ("without-native-traces", "trace_support")):
        ablated = deepcopy(scope)
        ablated[field] = {}
        ablated["scope_sha256"] = digest({k: v for k, v in ablated.items() if k != "scope_sha256"})
        results[name] = evaluate_native(ablated, policy)
    return results


def summarize_ablations(ablations: dict, labels: dict) -> dict:
    metrics = {}
    for name in ("native-traces-and-logs", "without-native-logs", "without-native-traces"):
        ranks, coverage, abstained, qualified = [], [], 0, 0
        for case in sorted(labels):
            result = ablations.get(case, {}).get(name)
            ranking = result["diagnostic_ranking"] if result else []
            root = labels[case]["root_cause_service"]
            ranks.append(ranking.index(root) + 1 if root in ranking else None)
            coverage.append(max((c["coverage"] for c in result["candidates"]), default=0) if result else None)
            abstained += bool(result and result["abstained"])
            qualified += bool(result and result["qualified_root"] is not None)
        n = len(labels)
        metrics[name] = {"selected_cases": n, "evaluated_cases": len(ablations),
            "top1": sum(r == 1 for r in ranks) / n if n else None,
            "top3": sum(r is not None and r <= 3 for r in ranks) / n if n else None,
            "mrr": sum(1 / r for r in ranks if r) / n if n else None,
            "abstentions": abstained, "abstention_rate": abstained / n if n else None,
            "qualified_predictions": qualified, "maximum_candidate_coverage_per_case": coverage,
            "ranking_metrics_include_abstained_hypotheses": True,
            "independent_sessions_established": False, "confidence_interval": None}
    return metrics


def evaluate_development(manifest_path: Path, profile: dict, policy: dict) -> dict:
    """Supplemental ablations of the ADR-013 canonical admitted development path."""
    from sentinelops.public_data.rcaeval_partitions import require_cases
    roles = require_cases(profile.get("cases", INSPECTED))
    if any(role != "development" for role in roles.values()):
        raise ValueError("native development evaluator rejects validation and sealed holdout before raw access")
    return evaluate_manifest(manifest_path, profile, policy, source_commit="recorded-by-caller",
                             include_ablations=True)


# Pure compatibility helper for the pre-ADR-013 recorded log-only experiment.
# Current manifest execution uses the canonical bounded native adapter above.
def diagnose_native(metric_times, metric_columns, log_rows, trace_rows, config, policy, source_refs):
    """Only telemetry and declared source config; no case/fault/root/injection input."""
    cutoff = max(metric_times)
    if not all(type(value) is int for value in metric_times) or metric_times != sorted(set(metric_times)):
        raise ValueError("native metric time must be strictly increasing Unix seconds")
    # Publisher naming convention is service_metric. It does not establish units.
    services = {name.split("_", 1)[0] for name in metric_columns if "_" in name}
    services.update(row["container_name"] for row in log_rows if isinstance(row.get("container_name"), str))
    services.update(row["serviceName"] for row in trace_rows if isinstance(row.get("serviceName"), str))
    if not services:
        raise ValueError("native source has no observed service identities")
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(token) for token in config["error_tokens"]) + r")\b", re.I)
    counts, hits, last = Counter(), Counter(), {}
    invalid_times = 0
    for row in log_rows:
        service, observed, message = row.get("container_name"), row.get("timestamp"), row.get("message")
        if service not in services or not isinstance(message, str):
            continue
        if type(observed) is not int or not min(metric_times) <= observed <= cutoff:
            invalid_times += 1
            continue
        counts[service] += 1
        hits[service] += bool(pattern.search(message))
        last[service] = max(observed, last.get(service, observed))
    facts, cards = [], []
    for service in sorted(services):
        components = {key: unavailable("native modality semantics not admitted for this component") for key in COMPONENTS}
        if counts[service]:
            fact = {"type": "log", "service": service, "observed_at": timestamp(datetime.fromtimestamp(last[service], timezone.utc)),
                    "source_ref": source_refs["logs"], "fact": f"Native captured logs: messages={counts[service]}, error-token matches={hits[service]}; pattern={config['log_pattern_version']}; incomplete sampling/receipts, not a health certificate"}
            fact["id"] = stable_id(fact); facts.append(fact)
            components["log_support"] = {"value": float(hits[service] > 0), "method": "native-log-pattern-presence/v1",
                                          "evidence_refs": [fact["id"]], "missing_reason": None}
        cards.append({"service": service, "components": components})
    full = rank_native_support(sorted(services), facts, cards, policy)
    ablated = deepcopy(cards)
    for card in ablated:
        card["components"]["log_support"] = unavailable("native log-support ablation")
    without = rank_native_support(sorted(services), [], ablated, policy)
    # Parent graph is observed, but native span kind/duration/receipt semantics
    # cannot be substituted for the validated local trace support profile.
    identities = {(row.get("traceID"), row.get("spanID")): row.get("serviceName") for row in trace_rows}
    edges = Counter()
    aligned = 0
    for row in trace_rows:
        parent = identities.get((row.get("traceID"), row.get("parentSpanID")))
        service = row.get("serviceName")
        if parent and service and parent != service:
            edges[parent + "->" + service] += 1
        if type(row.get("startTime")) is int and type(row.get("startTimeMillis")) is int:
            aligned += row["startTime"] // 1000 == row["startTimeMillis"]
    return {"diagnostic_version": "rcaeval-native-observations/v1", "cutoff_unix_seconds": cutoff,
        "result": full, "ablations": {"native-error-log-support": full, "without-native-log-support": without},
        "modality_counts": {"metric_rows": len(metric_times), "metric_channels": len(metric_columns), "logs": len(log_rows),
                            "traces": len(trace_rows), "admitted_log_messages": sum(counts.values()), "invalid_log_times": invalid_times,
                            "error_token_matches": sum(hits.values()), "trace_start_microseconds_milliseconds_aligned": aligned},
        "observed_parent_service_edges": dict(sorted(edges.items())),
        "missing_evidence": ["compatible IF/risk model", "verified SLO episode onset", "native duration/span-kind semantics",
                             "original receipt clocks", "observed deployment events", "native infrastructure capacity semantics"],
        "metric_values_not_used_as_IF": True, "trace_edges_not_scored_as_causal": True, "actions_allowed": False}
