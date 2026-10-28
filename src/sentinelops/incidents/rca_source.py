"""Read one captured source scope; no experiment truth, model fitting or actions."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
from tempfile import TemporaryDirectory

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.incidents.correlation_replay import replay_correlation
from sentinelops.incidents.health_replay import replay_health_manifest
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.trace_evidence import parent_id, span_tags, validate_trace
from sentinelops.trace_query import decode_v3_trace

CONTROL_KEYS = {"scenario_id", "root_cause_ground_truth", "root_causes_ground_truth", "affected_services_ground_truth", "fault_mode", "risk_label",
                "fault_start_at", "warmup_end_at", "recovery_start_at", "declared_split", "phase", "run_id", "session_id"}


def reject_control(value):
    if isinstance(value, dict):
        keys = {key.rsplit('.',1)[-1] for key in value}
        if keys & CONTROL_KEYS or (isinstance(value.get("key"),str) and value["key"].rsplit('.',1)[-1] in CONTROL_KEYS):
            raise ValueError("control/truth metadata is not operational RCA evidence")
        for item in value.values(): reject_control(item)
    elif isinstance(value, list):
        for item in value: reject_control(item)


def _body(report):
    return {key:value for key,value in report.items() if key not in ("report_ref", "source_commit")}


def verify_scope(telemetry: ArtifactStore, health_store: ArtifactStore, correlation_store: ArtifactStore,
                 correlation_ref: str) -> dict:
    correlated = correlation_store.read(correlation_ref)
    if correlated.get("report_version") != "slo-correlation-replay/v1":
        raise ValueError("RCA input is not a correlation replay report")
    correlation = correlated["correlation"]
    validate_payload("correlation-result.schema.json",correlation)
    health_ref = correlated["source_health_report_ref"]
    health = health_store.read(health_ref)
    with TemporaryDirectory(prefix="sentinelops-rca-lineage-") as temporary:
        from pathlib import Path
        root = Path(temporary)
        rebuilt_health = replay_health_manifest(telemetry,health["source_manifest_ref"],health["policy"],root/"health")
        if _body(rebuilt_health) != _body(health):
            raise ValueError("RCA upstream health does not reproduce from original raw telemetry")
        rebuilt = replay_correlation(health_store,health_ref,correlation["topology_snapshot"],root/"correlation")
        if _body(rebuilt) != _body(correlated):
            raise ValueError("RCA correlation differs from its verified health scope")
    source = telemetry.read(health["source_manifest_ref"])
    refs = source.get("evidence_refs")
    if not isinstance(refs,dict) or set(refs) != {"traces","logs","infrastructure"}:
        raise ValueError("RCA source needs an immutable multimodal evidence manifest")
    if not isinstance(refs["traces"],list) or len(refs["traces"])>200 or not isinstance(refs["infrastructure"],list) or len(refs["infrastructure"])>500:
        raise ValueError("RCA source modality scope exceeds its bounded profile")
    if any(len(set(refs[key]))!=len(refs[key]) for key in ("traces","infrastructure")):
        raise ValueError("RCA source modality references must not duplicate snapshots")
    telemetry_refs=set(source["raw_refs"]+source["observation_refs"]+source["feature_refs"]+source["quarantine_refs"]+
        refs["traces"]+refs["infrastructure"]+[refs["logs"],health["source_manifest_ref"],source.get("normalization_manifest_ref",health["source_manifest_ref"])])
    health_refs=set(health["signal_refs"]+[health_ref])
    objects={ref:telemetry for ref in telemetry_refs}
    objects.update({ref:health_store for ref in health_refs});objects[correlation_ref]=correlation_store
    allowed=set(objects)
    logs = telemetry.read(refs["logs"])
    if logs.get("kind") not in ("serving-logs","logs") or not isinstance(logs.get("records"),list) or len(logs["records"])>250000:
        raise ValueError("unsupported/bounded business log capture")
    signals = {}
    for ref in health["signal_refs"]:
        signal=health_store.read(ref);signals[signal["signal_id"]]=(ref,signal)
    return {"correlation":correlation,"health":health,"health_ref":health_ref,"source":source,"refs":refs,"allowed_refs":sorted(allowed),
            "source_objects":objects,
            "signals":signals,"logs":logs,"correlation_ref":correlation_ref,
            "traces":[(ref,telemetry.read(ref)) for ref in refs["traces"]],
            "infrastructure":[(ref,telemetry.read(ref)) for ref in refs["infrastructure"]]}


def epoch_ns(value: datetime) -> int:
    delta=value-datetime(1970,1,1,tzinfo=timezone.utc)
    return (delta.days*86400+delta.seconds)*1_000_000_000+delta.microseconds*1000


def from_ns(value: int) -> datetime:
    # Selection/cutoff checks use exact ns first; display timestamps floor to us.
    return datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(microseconds=value//1000)


def business_logs(scope: dict) -> tuple[list[dict],dict,Counter]:
    rows, lookup, identities, counts = [], {}, {}, Counter()
    for item in scope["logs"]["records"]:
        if not isinstance(item,dict) or item.get("event") != "request_completed" or item.get("service") not in BUSINESS_ROUTES:
            continue
        method,route=BUSINESS_ROUTES[item["service"]]
        if (item.get("method"),item.get("route")) != (method,route):
            continue
        reject_control(item)
        required=("timestamp_unix_ms","duration_ms","status_code","request_instance_id","trace_id","span_id")
        if any(key not in item for key in required):
            counts[item["service"]+"/invalid-completion"]+=1; continue
        ms,status,duration=item["timestamp_unix_ms"],item["status_code"],item["duration_ms"]
        try:
            finite_duration=not isinstance(duration,bool) and isinstance(duration,(int,float)) and math.isfinite(duration) and duration>=0
        except OverflowError:
            finite_duration=False
        if (type(ms) is not int or not 0<=ms<=253402300799999 or type(status) is not int or not 100<=status<=599
            or not finite_duration
            or any(not isinstance(item[key],str) or not item[key] for key in ("request_instance_id","trace_id","span_id"))):
            counts[item["service"]+"/invalid-completion"]+=1; continue
        key=(item["service"],item["request_instance_id"])
        fact=tuple(item[key] for key in required)+(item["method"],item["route"])
        if key in identities:
            if identities[key]!=fact:raise ValueError("conflicting duplicate business completion")
            counts["duplicate-completion"]+=1; continue
        identities[key]=fact;rows.append(item)
        span_key=(item["trace_id"],item["span_id"])
        if span_key in lookup and lookup[span_key]!=item:
            raise ValueError("conflicting completion logs for one server span")
        lookup[span_key]=item
    return rows,lookup,counts


def trace_observations(scope: dict, lookup: dict, lower: datetime, upper: datetime, as_of: datetime,
                       latency_ms: float) -> tuple[list[dict],Counter]:
    observed,counts=[],Counter()
    lower_ns,upper_ns=epoch_ns(lower),epoch_ns(min(upper,as_of))
    for reference,raw in scope["traces"]:
        received=raw.get("collected_at")
        log_received=scope["logs"].get("collected_at")
        receipts=[value for value in (received,log_received) if value is not None]
        if any(utc_time(value)>as_of for value in receipts):
            counts["trace-not-available"]+=1;continue
        # Matching completion logs are part of this evidence derivation too.
        received=max(receipts,key=utc_time) if len(receipts)==2 else None
        try:
            if raw.get("kind")!="trace":raise ValueError("wrong trace capture kind")
            frames=[json.loads(line) for line in raw["body"].splitlines() if line.strip()]
            reject_control(frames)
            trace_id=raw.get("trace_id") or next(span["traceId"] for frame in frames for resource in frame["result"]["resourceSpans"]
                for scope_spans in resource["scopeSpans"] for span in scope_spans["spans"])
            decoded=decode_v3_trace(raw["body"],trace_id);trace=decoded["data"][0]
            servers={};statuses={};correlations=set()
            for span in trace["spans"]:
                tags=span_tags(span);service=trace["processes"][span["processID"]]["serviceName"]
                if tags.get("span.kind")=="server" and service in BUSINESS_ROUTES:
                    log=lookup.get((trace_id,span["spanID"]))
                    if log is None or log["service"]!=service or log["status_code"]!=tags.get("http.response.status_code"):
                        raise ValueError("trace does not resolve to its business completion logs")
                    if log["timestamp_unix_ms"]*1_000_000>epoch_ns(as_of):
                        raise ValueError("matching completion is after the analysis cutoff")
                    statuses[service]=log["status_code"];correlations.add(log.get("correlation_id"));servers[service]=span
            if len(correlations)!=1 or None in correlations:raise ValueError("trace correlation is incomplete")
            entry=raw.get("request_entry_service","checkout-service")
            if entry!="checkout-service" and any(log["service"]!=entry for log in lookup.values() if log["trace_id"]==trace_id):
                raise ValueError("direct request scope conflicts with captured cascade completion logs")
            validate_trace(decoded,trace_id,next(iter(correlations)),statuses,require_timing=True,root_service=entry)
            if any(span["timing"]["end_unix_ns"]>epoch_ns(as_of) for span in trace["spans"]):
                counts["trace-spans-after-cutoff"]+=1;continue
            root=servers[entry]
            if not lower_ns<=root["timing"]["end_unix_ns"]<=upper_ns:
                counts["trace-outside-window"]+=1;continue
            if root["timing"]["duration_ns"]<=0:raise ValueError("zero reference span duration")
            if not any(statuses[s]>=500 or span["timing"]["duration_ns"]>latency_ms*1_000_000 for s,span in servers.items()):
                counts["captured-trace-without-observed-violation"]+=1;continue
            children={s:[] for s in servers}
            for span in trace["spans"]:
                if span_tags(span).get("span.kind")=="client":
                    for service,server in servers.items():
                        if span["processID"]==server["processID"] and parent_id(span)==server["spanID"]:
                            children[service].append(span)
            observed.append({"reference":reference,"received_at":received,"trace_id":trace_id,"servers":servers,
                "children":children,"statuses":statuses,"root_duration_ns":root["timing"]["duration_ns"],"entry_service":entry})
        except (ValueError,KeyError,TypeError,StopIteration,IndexError):
            counts["unsupported-or-incomplete-trace"]+=1
    return observed,counts
