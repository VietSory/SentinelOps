"""Native RCAEval development only; truth never enters diagnostic scoring."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from sentinelops.incidents.rca import COMPONENTS, rank_native_support, unavailable
from sentinelops.public_data.audit import load_verified_manifest, require_verified_file
from sentinelops.public_data.rcaeval_partitions import require_cases, read_index
from sentinelops.telemetry.core import stable_id, timestamp


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


def evaluate_development(manifest_path: Path, config: dict, policy: dict):
    import pyarrow.parquet as pq
    roles = require_cases(config["cases"])
    if any(role != "development" for role in roles.values()) or config.get("holdout_opened") is not False:
        raise ValueError("native development evaluator rejects validation and sealed holdout before raw access")
    index, _ = read_index(manifest_path)
    manifest, files = load_verified_manifest(manifest_path)
    if set(manifest["selection"]) != set(config["cases"]):
        raise ValueError("native evaluation source scope differs from predeclared development cases")
    root = manifest_path.parent.parent / "rcaeval" / manifest["revision"]
    diagnostics = []
    for name in config["cases"]:
        directory = root / name
        metric = pq.read_table(require_verified_file(files, directory / "metrics.parquet"))
        log_path, trace_path = directory / "logs.parquet", directory / "traces.parquet"
        logs = pq.read_table(require_verified_file(files, log_path)).to_pylist() if log_path.resolve() in files.values() else []
        traces = pq.read_table(require_verified_file(files, trace_path)).to_pylist() if trace_path.resolve() in files.values() else []
        refs = {key: "public-file-sha256://" + hashlib.sha256(path.read_bytes()).hexdigest() for key, path in
                (("metrics", directory / "metrics.parquet"), ("logs", log_path), ("traces", trace_path)) if path.resolve() in files.values()}
        diagnostic = diagnose_native(metric["time"].to_pylist(), [c for c in metric.column_names if c != "time"], logs, traces, config, policy, refs)
        diagnostics.append({"case": name, "diagnostic": diagnostic})
    # Scoring is finished before evaluator-only metadata is joined.
    truth = {row["case"]: row for row in index}
    results = {}
    for ablation in ("native-error-log-support", "without-native-log-support"):
        correct1, correct3, reciprocal, abstentions, coverage = 0, 0, 0., 0, []
        for row in diagnostics:
            result = row["diagnostic"]["ablations"][ablation]
            ranking = result["ranking"]
            root_service = truth[row["case"]]["root_cause_service"]
            rank = ranking.index(root_service) + 1 if root_service in ranking else None
            correct1 += rank == 1; correct3 += rank is not None and rank <= 3
            reciprocal += 1 / rank if rank else 0
            abstentions += result["abstained"]
            coverage.append(max((c["coverage"] for c in result["candidates"]), default=0))
        n = len(diagnostics)
        results[ablation] = {"cases": n, "top1": correct1 / n, "top3": correct3 / n, "mrr": reciprocal / n,
            "abstentions": abstentions, "abstention_rate": abstentions / n, "maximum_candidate_coverage_per_case": coverage,
            "independent_sessions_established": False, "confidence_interval": None}
    return {"evaluation_version": "rcaeval-native-development/v1", "source_manifest_sha256": manifest_path.stem,
            "config_sha256": stable_id(config), "policy_sha256": stable_id(policy), "cases": diagnostics, "metrics": results,
            "holdout_opened": False, "model_outputs_available": False, "full_G4_accepted": False, "actions_allowed": False,
            "limitations": ["Three previously inspected development cases, not independent or final accuracy trials.",
                "Rankings are weak native error-log presence hypotheses, including abstentions in every denominator.",
                "Metrics are read for timestamps/service scope; no numeric channel is mislabelled IF inference.",
                "Native parent edges are retained but unsupported duration/span-kind semantics receive no trace weight.",
                "Topology-only/all-ML comparisons unavailable; only actual log evidence is ablated.",
                "Cascade/independent-failure accuracy is not established by these three single-root development cases."]}
