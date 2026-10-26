"""Reconstruct serving features from raw artifacts before dataset admission."""
from __future__ import annotations

from sentinelops.contracts import validate_payload
from sentinelops.telemetry.core import BUSINESS_ROUTES
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.trace_query import decode_v3_trace
from sentinelops.trace_evidence import validate_trace

TRUTH_FIELDS = {"scenario_id","root_cause_ground_truth","affected_services_ground_truth","fault_start_at",
                "warmup_end_at","recovery_start_at","declared_split","risk_label","phase","run_id","session_id","fault_mode"}


def reject_truth(value):
    if isinstance(value,dict):
        if TRUTH_FIELDS.intersection(value):
            raise ValueError("experiment truth/control metadata was found in serving evidence")
        for item in value.values(): reject_truth(item)
    elif isinstance(value,list):
        for item in value: reject_truth(item)


def verify_source_replay(store,manifest: dict,run: dict) -> list[dict]:
    """Checksum every referenced object and compare a fresh raw→feature replay.

    This verifies captured contents/provenance, not an external source's honesty.
    Do not silently accept caller-authored features just because their own hash is valid.
    """
    try:
        reference = manifest.get("normalization_manifest_ref")
        if not isinstance(reference,str):
            raise ValueError("source manifest lacks raw replay provenance")
        original = store.read(reference)
        if any(manifest.get(key)!=value for key,value in original.items()):
            raise ValueError("aggregate source manifest differs from its normalization revision")
        captures = [store.read(ref) for ref in original["raw_refs"]]
        if not captures:
            raise ValueError("source manifest has no raw captures")
        for capture in captures: reject_truth(capture)
        replayed = process_captures(captures,store,environment=run["environment"])
        if replayed["manifest_ref"]!=reference:
            raise ValueError("raw replay does not reproduce the stored observations/features")
        for kind in ("observation_refs","feature_refs","quarantine_refs"):
            for ref in original[kind]: reject_truth(store.read(ref))
        evidence = manifest["evidence_refs"]
        if evidence["logs"]!=run["logs_ref"] or evidence["infrastructure"]!=run["infrastructure_refs"]:
            raise ValueError("source evidence links differ from run truth")
        logs = store.read(evidence["logs"])
        if logs.get("kind")!="serving-logs":
            raise ValueError("source logs are not a serving evidence capture")
        reject_truth(logs)
        trace_ids = {item.get("trace_id") for item in logs["records"]}
        traces = evidence["traces"]
        if not isinstance(traces,list) or not traces or len(set(traces))!=len(traces):
            raise ValueError("run needs distinct complete business trace snapshots")
        for ref in traces:
            raw = store.read(ref)
            if raw.get("kind")!="trace" or raw["trace_id"] not in trace_ids:
                raise ValueError("trace snapshot does not link to the run's serving logs")
            decoded = decode_v3_trace(raw["body"],raw["trace_id"])
            facts = {}
            for item in logs["records"]:
                if item.get("trace_id")==raw["trace_id"] and item.get("event")=="request_completed" and item.get("service") in BUSINESS_ROUTES:
                    service=item["service"]
                    fact=(item["correlation_id"],item["span_id"],item["status_code"])
                    if service in facts and facts[service]!=fact:
                        raise ValueError("trace has conflicting server completion logs")
                    facts[service]=fact
            if set(facts)!=set(BUSINESS_ROUTES) or len({fact[0] for fact in facts.values()})!=1:
                raise ValueError("trace snapshot lacks correlated business completion logs")
            servers=validate_trace(decoded,raw["trace_id"],facts["checkout-service"][0],{service:fact[2] for service,fact in facts.items()})
            if any(servers[service]["spanID"]!=fact[1] for service,fact in facts.items()):
                raise ValueError("trace/log server span identities differ")
            trace = decoded["data"][0]
            services = set()
            for span in trace["spans"]:
                service = trace["processes"][span["processID"]]["serviceName"]
                tags = {tag["key"]:tag["value"] for tag in span["tags"]}
                reject_truth(tags)
                if service in BUSINESS_ROUTES and tags.get("span.kind")=="server":
                    method,route = BUSINESS_ROUTES[service]
                    if tags.get("http.request.method")==method and tags.get("http.route")==route:
                        services.add(service)
            if services!=set(BUSINESS_ROUTES):
                raise ValueError("trace snapshot lacks complete business server routes")
        for ref in evidence["infrastructure"]:
            item = store.read(ref)
            validate_payload("infrastructure-observation.schema.json",item)
            if item["environment"]!=run["environment"]:
                raise ValueError("infrastructure source environment differs")
        return [store.read(reference) for reference in original["feature_refs"]]
    except (FileNotFoundError,KeyError,TypeError) as exc:
        raise ValueError("source lineage is incomplete or malformed") from exc
