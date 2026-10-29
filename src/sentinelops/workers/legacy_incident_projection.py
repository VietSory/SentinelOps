"""Offline projection compatibility for the pre-merge measured prototype.

Source silence never finalizes a cluster. Each accepted bucket is verified from
raw captures before the existing event-time reducer sees it. Files may be
orphaned by a failed transaction; only the database ledger accepts a commit.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import time

import asyncpg

from sentinelops.contracts import utc_time
from sentinelops.incidents.correlation import topology_paths
from sentinelops.incidents.domain import IncidentWrite
from sentinelops.incidents.rca import validate_policy, evaluate_rca
from sentinelops.incidents.rca_replay import business_logs, derive_group
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.incident_state import advance_groups
from sentinelops.workers.model_signals import collect_model_signals, add_model_support


def evaluate_cluster(store, closed, *, policy, as_of, health_policy, modalities=None, include_slo_values=False):
    """Reuse native evidence derivation; no experiment truth or invented scores."""
    correlation = closed["correlation"]
    correlation_ref = store.write("manifests", correlation)
    scope_ref = store.write("manifests", closed["scope"])
    if scope_ref != closed["scope_ref"]:
        raise ValueError("continuous cluster identity differs")
    if modalities is None:
        modalities = {"logs": store.write("raw", {"kind": "serving-logs", "records": []}),
                      "traces": [], "infrastructure": []}
    if set(modalities) != {"logs", "traces", "infrastructure"} or len(modalities["traces"]) > 200 or len(modalities["infrastructure"]) > 500:
        raise ValueError("continuous evidence exceeds the native bounded profile")
    signals = {item["signal"]["signal_id"]: (item["signal_ref"], item["signal"]) for item in closed["opening_objects"]}
    refs = {scope_ref, correlation_ref, modalities["logs"], *modalities["traces"], *modalities["infrastructure"]}
    for ref, signal in signals.values():
        if store.read(ref) != signal:
            raise ValueError("continuous opening differs from verified source object")
        refs.add(ref)
        refs.update(signal["evidence_refs"])
    for ref in refs:
        store.read(ref)
    logs = store.read(modalities["logs"])
    if logs.get("kind") not in ("logs", "serving-logs") or not isinstance(logs.get("records"), list) or len(logs["records"]) > 250000:
        raise ValueError("unsupported continuous completion logs")
    scope = {"correlation": correlation, "correlation_ref": correlation_ref,
             "health": {"policy": health_policy}, "source": {"environment": correlation["source_identity"][0]},
             "signals": signals, "refs": modalities, "allowed_refs": sorted(refs), "logs": logs,
             "traces": [(ref, store.read(ref)) for ref in modalities["traces"]],
             "infrastructure": [(ref, store.read(ref)) for ref in modalities["infrastructure"]]}
    rows, lookup, counts = business_logs(scope)
    results = []
    for group in correlation["groups"]:
        row = derive_group(store, scope, group, policy, as_of, rows, lookup, counts)
        if include_slo_values:
            result = row["rca"]
            facts, availability = deepcopy(result["evidence"]), deepcopy(result["evidence_availability"])
            for signal_id in group["signal_ids"]:
                _, signal = signals[signal_id]
                for ref in signal["evidence_refs"]:
                    observation = store.read(ref)
                    values = observation["payload"]
                    if values is None:
                        continue
                    fact = {"type": "metric", "service": signal["service"], "observed_at": observation["bucket_end"],
                            "source_ref": ref, "fact": f"Accepted SLO observation: p95_latency_ms={values['p95_latency_ms']}; error_rate={values['error_rate']}; latency_limit_ms={health_policy['latency_ms']}; error_rate_limit={health_policy['error_rate']}; SLO rule, not model inference"}
                    fact["id"] = stable_id({"group_id": group["group_id"], **fact})
                    if fact["id"] not in availability:
                        facts.append(fact)
                        availability[fact["id"]] = max(observation["available_at"], observation["ingested_at"], key=utc_time)
            row["rca"] = evaluate_rca(correlation, group["group_id"], facts,
                [{"service": c["service"], "components": c["components"]} for c in result["candidates"]], policy,
                as_of=as_of, evidence_availability=availability, source_refs=scope["allowed_refs"])
        row["rca_ref"] = store.write("manifests", row["rca"])
        results.append(row)
    return results


def incident_commands(consumer_id, closed, results, detected_at, model_signals=()):
    commands = []
    for row in results:
        result = row["rca"]
        group = next(g for g in closed["correlation"]["groups"] if g["group_id"] == row["group_id"])
        identifier = "SLO-" + stable_id({"consumer_id": consumer_id, "group_id": group["group_id"]})
        detail = {"type": "timeline", "service": None, "observed_at": detected_at,
                  "source_ref": row["rca_ref"], "fact": "Continuous SLO consumer; analytical RCA hypotheses; actions_allowed=false; "
                  "IF/XGBoost unavailable; abstention=" + ",".join(result["abstention_reasons"])}
        detail["id"] = stable_id(detail)
        available_models = [(ref, s) for ref, s in model_signals if s["status"] == "AVAILABLE" and s["service"] in group["affected_services"]]
        if available_models:
            detail["fact"] = "Continuous SLO consumer; analytical RCA hypotheses; actions_allowed=false; validated model versions=" + ",".join(sorted({s["inference"]["model_version"] for _, s in available_models}))
            detail["id"] = stable_id({k: v for k, v in detail.items() if k != "id"})
        evidence = [*deepcopy(result["evidence"]), detail]
        candidates = {c["service"]: c for c in result["candidates"]}
        ranking = [{k: candidates[s][k] for k in ("service", "score", "evidence_refs")} for s in result["ranking"]]
        context = {"availability": "UNKNOWN", "recovery_source": None,
                   "source_refs": sorted({closed["scope_ref"], row["rca_ref"]}),
                   "config_refs": ["config://rca-policy/" + result["policy_sha256"]],
                   "trigger_sources": [{"source": "slo", "signal_id": item["signal"]["signal_id"],
                       "emitted_at": item["signal"]["evaluated_at"], "source_ref": item["signal_ref"]}
                       for item in closed["opening_objects"] if item["signal"]["signal_id"] in group["signal_ids"]]}
        for revision, stage in enumerate(("OPEN", "RCA_READY", "ESCALATED")):
            payload = {"schema_version": "1.0", "incident_id": identifier, "detected_at": detected_at,
                       "state": "escalated" if stage == "ESCALATED" else "incident", "severity": row["severity"],
                       "affected_services": group["affected_services"], "service_signals": [
                           {"service": s["service"], "anomaly_score": s["inference"]["detection"]["anomaly_score"],
                            "degradation_risk": s["inference"]["risk"]["risk_score"] if s["inference"]["risk"] is not None else None}
                           for _, s in available_models],
                       "root_cause_ranking": ranking if revision else [], "evidence": evidence}
            command = IncidentWrite(payload, stage, revision, identifier + ":" + stage,
                "continuous-incident-worker", "verified SLO source; analytical RCA; operator review required", deepcopy(context))
            command.validate()
            commands.append(command)
    return commands
