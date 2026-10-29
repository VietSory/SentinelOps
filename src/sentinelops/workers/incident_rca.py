"""Derive continuous RCA only after the journal authenticates an accepted job.

This module verifies frozen capture integrity/selection/receipt clocks and shares
the analytical RCA math. The PostgreSQL/source-lineage boundary lives in
IncidentJournal.verified_job; a client-provided group is not that authority.
"""
from __future__ import annotations

from datetime import timedelta
import json

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.incidents.rca import validate_policy
from sentinelops.incidents.rca_replay import derive_group
from sentinelops.incidents.rca_source import business_logs, reject_control
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id
from sentinelops.workers.incident_evidence import (
    LOG_RECORDS, TRACE_BYTES, TRACES_PER_ENTRY, completion_records, select_traces,
)
from sentinelops.workers.model_signals import verify_model_evidence, add_model_support


def derive_continuous(store, *, intent_ref: str, intent: dict, closed: dict, capture_ref: str,
                      profile: dict, health_identity: dict, health_policy: dict, model_registry=None) -> dict:
    """Pure replay of one accepted job's frozen operational evidence."""
    policy = intent["rca_policy"]
    validate_policy(policy)
    correlation = closed["correlation"]
    validate_payload("correlation-result.schema.json", correlation)
    if (store.read(intent_ref) != intent or store.read(intent["correlation_ref"]) != correlation
        or store.read(intent["scope_ref"]) != closed["scope"]
        or correlation["source_scope_sha256"] != stable_id(closed["scope"])
        or intent["opening_objects"] != closed["opening_objects"]
        or closed["scope"]["health_stream_id"] != stable_id(health_identity)):
        raise ValueError("continuous RCA job differs from its verified accepted source scope")
    group = next(g for g in correlation["groups"] if g["group_id"] == intent["group_id"])
    capture = store.read(capture_ref)
    has_models = capture.get("capture_version") == "continuous-incident-evidence/v2"
    if (set(capture) != ({"capture_version", "job_id", "intent_ref", "profile", "health_stream_id", "window_start",
                         "window_end", "analyzed_at", "evidence_refs", "source_containers", "missing_counts", "actions_allowed"}
                         | ({"model_evidence"} if has_models else set()))
        or capture["capture_version"] not in ("continuous-incident-evidence/v1", "continuous-incident-evidence/v2")
        or capture["job_id"] != intent["job_id"] or capture["intent_ref"] != intent_ref
        or capture["health_stream_id"] != stable_id(health_identity)
        or capture["profile"] != profile or capture["actions_allowed"] is not False):
        raise ValueError("operational capture belongs to another accepted job/profile/source")
    reject_control(capture)
    decision = utc_time(capture["analyzed_at"])
    lower, upper = utc_time(capture["window_start"]), utc_time(capture["window_end"])
    if (lower != utc_time(group["first_onset_at"]) or upper < lower
        or upper > min(decision, lower + timedelta(seconds=policy["evidence_window_seconds"]))
        or decision < max(utc_time(intent["detected_at"]), utc_time(correlation["as_of"]))):
        raise ValueError("operational evidence window or actual analysis clock is inconsistent")
    refs = capture["evidence_refs"]
    if (set(refs) != {"logs", "traces", "infrastructure"}
        or not isinstance(refs["traces"], list) or len(refs["traces"]) > len(BUSINESS_ROUTES) * TRACES_PER_ENTRY
        or len(set(refs["traces"])) != len(refs["traces"])
        or not isinstance(refs["infrastructure"], list) or len(refs["infrastructure"]) > 1):
        raise ValueError("operational evidence references exceed the capture profile")
    logs = store.read(refs["logs"])
    if (set(logs) != {"kind", "projection_version", "records", "collected_at", "source_receipts"}
        or logs["kind"] != "serving-logs" or logs["projection_version"] != "business-completion/v1"
        or not isinstance(logs["records"], list) or len(logs["records"]) > len(BUSINESS_ROUTES) * LOG_RECORDS
        or not isinstance(logs["source_receipts"], list) or len(logs["source_receipts"]) != len(BUSINESS_ROUTES)):
        raise ValueError("unsupported bounded business completion projection")
    reject_control(logs)
    log_received = utc_time(logs["collected_at"])
    receipts = {item["service"]: item for item in logs["source_receipts"]}
    if (set(receipts) != set(BUSINESS_ROUTES) or log_received > decision
        or any(utc_time(item["collected_at"]) > log_received for item in receipts.values())
        or any(row.get("service") not in BUSINESS_ROUTES for row in logs["records"])):
        raise ValueError("completion receipt coverage or cutoff differs from the actual capture")
    for service, receipt in receipts.items():
        expected = capture["source_containers"][service]
        if any(receipt.get(key) != expected[key] for key in ("service", "container_id", "compose_project", "running")):
            raise ValueError("business completion receipt differs from its owned source")
        records = [r for r in logs["records"] if r["service"] == service]
        rebuilt, _ = completion_records(b"\n".join(json.dumps(r, separators=(",", ":"), allow_nan=False).encode() for r in records),
            service, lower, upper, utc_time(receipt["collected_at"]))
        if rebuilt != records or records and (receipt.get("unavailable") or "quarantined-service-log-capture" in receipt.get("selection", {})):
            raise ValueError("completion projection contains unavailable, unsafe or out-of-window records")
    traces = [(ref, store.read(ref)) for ref in refs["traces"]]
    selection = set(select_traces(logs["records"], health_policy["latency_ms"]))
    seen = set()
    for _, trace in traces:
        key = (trace["trace_id"], trace["request_entry_service"])
        if (key not in selection or key in seen or trace["kind"] != "trace"
            or not isinstance(trace["body"], str) or len(trace["body"].encode()) > TRACE_BYTES
            or utc_time(trace["collected_at"]) > decision):
            raise ValueError("trace selection/receipt escapes the captured business completion scope")
        reject_control([json.loads(line) for line in trace["body"].splitlines() if line.strip()])
        seen.add(key)
    infrastructure = [(ref, store.read(ref)) for ref in refs["infrastructure"]]
    for _, point in infrastructure:
        if (point["source_ref"] != profile["infrastructure_source"]
            or utc_time(point["available_at"]) > decision):
            raise ValueError("infrastructure source/receipt differs from the selected profile")
    allowed = {intent_ref, intent["scope_ref"], intent["correlation_ref"], capture_ref,
               refs["logs"], *refs["traces"], *refs["infrastructure"]}
    signals = {}
    for item in closed["opening_objects"]:
        ref, signal = item["signal_ref"], item["signal"]
        if store.read(ref) != signal:
            raise ValueError("verified opening artifact changed during RCA derivation")
        signals[signal["signal_id"]] = (ref, signal)
        allowed.add(ref)
        for observation_ref in signal["evidence_refs"]:
            observation = store.read(observation_ref)
            allowed.add(observation_ref)
            for raw_ref in observation["provenance"]["source_refs"]:
                store.read(raw_ref)
                allowed.add(raw_ref)
    scope = {"correlation": correlation, "correlation_ref": intent["correlation_ref"],
        "health": {"policy": health_policy}, "signals": signals, "allowed_refs": sorted(allowed),
        "source": {"environment": health_identity["environment"]}, "refs": refs, "logs": logs,
        "traces": traces, "infrastructure": infrastructure}
    rows, lookup, counts = business_logs(scope)
    derived = derive_group(store, scope, group, policy, decision, rows, lookup, counts,
                           clock_mode="actual-health-decision")
    if has_models:
        signals = verify_model_evidence(store, capture, model_registry)
        derived = add_model_support(store, derived, signals, policy=policy, as_of=decision)
        derived["model_signals"] = signals
    return derived
