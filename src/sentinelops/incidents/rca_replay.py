"""Derive bounded RCA evidence from verified local captures, without fault truth."""
from __future__ import annotations

from collections import Counter,defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import median
import subprocess

from sentinelops.contracts import utc_time,validate_payload
from sentinelops.incidents.correlation import topology_paths
from sentinelops.incidents.rca import COMPONENTS,evaluate_rca,temporal_precedence,unavailable,validate_policy
from sentinelops.incidents.rca_source import business_logs,epoch_ns,from_ns,trace_observations,verify_scope
from sentinelops.telemetry.core import BUSINESS_ROUTES,stable_id,timestamp
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.trace_evidence import span_tags


class Evidence:
    def __init__(self,group,allowed):
        self.group=group;self.allowed=allowed;self.facts={};self.available={}

    def add(self,kind,service,observed,reference,text,available):
        if reference not in self.allowed:raise ValueError("fact source escapes the verified evidence scope")
        body={"type":kind,"service":service,"observed_at":observed,"source_ref":reference,"fact":text}
        identifier=stable_id({"group_id":self.group["group_id"],**body})
        self.facts[identifier]={"id":identifier,**body};self.available[identifier]=available
        return identifier


def card(value,method,refs):
    return {"value":float(value),"method":method,"evidence_refs":sorted(set(refs)),"missing_reason":None}


def _trace_support(trace,service):
    """Observed local residual is a lower bound, never a CPU/causality measure."""
    statuses=trace["statuses"];servers=trace["servers"];children=trace["children"]
    errors=any(status>=500 for status in statuses.values())
    duration_method=("native-direct-span-duration-share/v1" if trace.get("entry_service","checkout-service")!="checkout-service"
                     else "native-span-duration-share/v1")
    def contained(owner,span):
        parent=servers[owner]["timing"];timing=span["timing"]
        return parent["start_unix_ns"]<=timing["start_unix_ns"]<=timing["end_unix_ns"]<=parent["end_unix_ns"]
    if service=="rds":
        db=[span for entries in children.values() for span in entries
            if span_tags(span).get("peer.service")=="rds" and span_tags(span).get("db.system.name")=="postgresql"]
        if not db:return None
        if any(not contained(owner,span) for owner,entries in children.items() for span in entries if span in db):return None
        if errors:
            value=float(any(span_tags(span).get("error") is True for span in db))
            return value,"native-span-terminal-error/v1",f"observed_postgresql_client_errors={int(value)}"
        value=min(1.,sum(span["timing"]["duration_ns"] for span in db)/trace["root_duration_ns"])
        detail=f"postgresql_client_duration_ns={sum(s['timing']['duration_ns'] for s in db)}; reference_server_duration_ns={trace['root_duration_ns']}"
        if duration_method=="native-direct-span-duration-share/v1":detail+="; reference_entry_service="+trace["entry_service"]
        return value,duration_method,detail
    if service not in servers:return None
    server=servers[service];calls=children[service]
    if any(not contained(service,span) for span in calls):return None
    if errors:
        local_error=statuses[service]>=500 and not any(span_tags(span).get("error") is True for span in calls)
        return float(local_error),"native-span-terminal-error/v1",f"http_status={statuses[service]}; observed_direct_child_errors={sum(span_tags(s).get('error') is True for s in calls)}"
    if service!="checkout-service" and not any(span_tags(span).get("peer.service")=="rds" and span_tags(span).get("db.system.name")=="postgresql" for span in calls):
        return None
    total=sum(span["timing"]["duration_ns"] for span in calls)
    residual=max(0,server["timing"]["duration_ns"]-total)
    return min(1.,residual/trace["root_duration_ns"]),duration_method,f"server_duration_ns={server['timing']['duration_ns']}; sum_observed_direct_client_duration_ns={total}; observed_residual_lower_bound_ns={residual}; reference_server_duration_ns={trace['root_duration_ns']}"


def derive_group(telemetry,scope,group,policy,as_of,rows,lookup,log_counts):
    correlation=scope["correlation"];paths=topology_paths(correlation["topology_snapshot"])
    services=sorted(set().union(*(paths[service].keys() for service in group["affected_services"])))
    lower=utc_time(group["first_onset_at"]);upper=min(as_of,lower+timedelta(seconds=policy["evidence_window_seconds"]))
    evidence=Evidence(group,set(scope["allowed_refs"]));components={service:{
        "anomaly_strength":unavailable("no compatible IF/infra observation in this source scope"),
        "temporal_precedence":unavailable("overlapping onset intervals or no observed onset"),
        "dependency_trace_relevance":unavailable("no usable in-window timing-verified failing trace"),
        "log_support":unavailable("no eligible captured business completion evidence"),
        "recent_change_support":unavailable("no observed deployment/config event; controls are not change evidence")
    } for service in services}
    counts=Counter();onsets={}
    evidence.add("topology",None,None,scope["correlation_ref"],
        f"Configured dependency snapshot {correlation['topology_sha256']} supports analytical group {group['group_id']}; observed deployment time unavailable",None)
    for signal_id in group["signal_ids"]:
        ref,signal=scope["signals"][signal_id]
        observations=[telemetry.read(value) for value in signal["evidence_refs"]]
        available=max((observation[key] for observation in observations for key in ("available_at","ingested_at")),key=utc_time) if observations else None
        identifier=evidence.add("timeline",signal["service"],signal["episode_start_at"],ref,
            f"Observed SLO episode bucket onset {signal['episode_start_at']}; confirmation evaluated at replay clock {signal['episode_confirmed_at']}, not historical emission",available)
        onsets[signal["service"]]=identifier
    for service in services:
        value=temporal_precedence(correlation,group,service,policy)
        if value is not None:
            components[service]["temporal_precedence"]=card(value,"disjoint-onset-intervals/v1",list(onsets.values()))
    log_received=scope["logs"].get("collected_at")
    for service in services:
        if service not in BUSINESS_ROUTES:continue
        selected=[row for row in rows if row["service"]==service and epoch_ns(lower)//1_000_000<=row["timestamp_unix_ms"]<=epoch_ns(upper)//1_000_000]
        if not selected or log_counts[service+"/invalid-completion"] or (log_received is not None and utc_time(log_received)>as_of):
            counts[service+"/log-support-unavailable"]+=1;continue
        bad=[row for row in selected if row["status_code"]>=500 or row["duration_ms"]>scope["health"]["policy"]["latency_ms"]]
        observed=max(row["timestamp_unix_ms"] for row in selected)
        identifier=evidence.add("log",service,timestamp(from_ns(observed*1_000_000)),scope["refs"]["logs"],
            f"In captured business completions in [{timestamp(lower)},{timestamp(upper)}]: count={len(selected)}, slow_or_5xx={len(bad)}; presence in this snapshot is support, not a health certificate",log_received)
        components[service]["log_support"]=card(bool(bad),"business-completion-support/v1",[identifier])
    traces,trace_counts=trace_observations(scope,lookup,lower,upper,as_of,scope["health"]["policy"]["latency_ms"])
    counts.update(trace_counts)
    supports=defaultdict(list)
    for trace in traces:
        if not set(trace["servers"])&set(services):
            counts["trace-outside-group-dependency-path"]+=1;continue
        for service in services:
            support=_trace_support(trace,service)
            if support is None:continue
            value,method,detail=support
            root_end=max(span["timing"]["end_unix_ns"] for span in trace["servers"].values())
            identifier=evidence.add("trace",service,timestamp(from_ns(root_end)),trace["reference"],
                f"Trace {trace['trace_id']}: {detail}; bounded observed dependency support, not proof of causality",trace["received_at"])
            supports[service].append((value,method,identifier))
    for service,entries in supports.items():
        errors=[entry for entry in entries if entry[1]=="native-span-terminal-error/v1"]
        direct=[entry for entry in entries if entry[1]=="native-direct-span-duration-share/v1"]
        chosen=errors or direct or entries
        components[service]["dependency_trace_relevance"]=card(median(entry[0] for entry in chosen),chosen[0][1],[entry[2] for entry in chosen])
    if "rds" in services:
        points=[]
        for reference,observation in scope["infrastructure"]:
            validate_payload("infrastructure-observation.schema.json",observation)
            identity={k:v for k,v in observation.items() if k not in ("record_id","available_at")}
            if stable_id(identity)!=observation["record_id"]:raise ValueError("infrastructure observation identity differs")
            if (observation["environment"]==scope["source"]["environment"] and lower<=utc_time(observation["observed_at"])<=upper
                and utc_time(observation["available_at"])<=as_of):points.append((reference,observation))
        if points:
            reference,point=max(points,key=lambda item:item[1]["metrics"]["db_connections_pct"])
            metrics=point["metrics"];value=min(1.,metrics["db_connections_pct"]/100)
            identifier=evidence.add("metric","rds",point["observed_at"],reference,
                f"Observed client-connection capacity pressure: {metrics['db_connections_count']}/({metrics['max_connections']}-{metrics['reserved_connections']}) = {metrics['db_connections_pct']} percent; fixed capacity fraction rule, not IF or overall DB health",point["available_at"])
            components["rds"]["anomaly_strength"]=card(value,"infra-connection-pressure/v1",[identifier])
    result=evaluate_rca(correlation,group["group_id"],list(evidence.facts.values()),
        [{"service":service,"components":value} for service,value in components.items()],policy,as_of=as_of,
        evidence_availability=evidence.available,source_refs=scope["allowed_refs"])
    critical=False
    for signal_id in group["signal_ids"]:
        _,signal=scope["signals"][signal_id]
        if signal["service"]!="checkout-service":continue
        observed=sorted((telemetry.read(ref) for ref in signal["evidence_refs"]),key=lambda item:item["bucket_end"])
        critical=(len(observed)>=2 and all(item["quality"]["eligible_for_inference"] and item["payload"]["error_rate"]>=.20 for item in observed[-2:])
                  and utc_time(observed[-1]["bucket_end"])-utc_time(observed[-2]["bucket_end"])==timedelta(seconds=60))
    severity="critical" if critical else "high" if "checkout-service" in group["affected_services"] or len(group["affected_services"])>=2 else "medium"
    return {"group_id":group["group_id"],"rca":result,"severity":severity,"source_counts":dict(counts)}


def replay_rca(telemetry: ArtifactStore,health_store: ArtifactStore,correlation_store: ArtifactStore,
               correlation_ref: str,policy: dict,output: Path,*,project: Path|None=None,as_of=None) -> dict:
    validate_policy(policy);commit=None
    if project is not None:
        commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=project,text=True).strip()
        if subprocess.check_output(["git","status","--porcelain"],cwd=project,text=True).strip():
            raise ValueError("commit RCA source adapter/policy before actual replay")
    scope=verify_scope(telemetry,health_store,correlation_store,correlation_ref)
    decision=as_of or utc_time(scope["correlation"]["as_of"])
    rows,lookup,counts=business_logs(scope)
    groups=scope["correlation"]["groups"]
    if len(groups)>100:raise ValueError("RCA replay exceeds its bounded group profile")
    results=[derive_group(telemetry,scope,group,policy,decision,rows,lookup,counts) for group in groups]
    report={"report_version":"local-evidence-rca-replay/v1","mode":"event-time-analytical","source_commit":commit,
        "source_correlation_ref":correlation_ref,"source_health_ref":scope["health_ref"],"policy":policy,
        "source_telemetry_manifest_ref":scope["health"]["source_manifest_ref"],"policy_sha256":stable_id(policy),
        "as_of":timestamp(decision),"group_count":len(groups),"results":results,"log_source_counts":dict(counts),
        "raw_health_correlation_lineage_verified":True,"model_outputs_available":False,"actions_allowed":False,
        "historical_emissions_verified":False,"source_artifact_refs":sorted(scope["source_objects"])}
    output.mkdir(parents=True,exist_ok=False)
    destination=ArtifactStore(output)
    for reference,origin in sorted(scope["source_objects"].items()):
        kind=reference.split('/')[2]
        if destination.write(kind,origin.read(reference))!=reference:
            raise ValueError("source snapshot changed during RCA evidence archival")
    for row in results:
        row["rca_ref"]=destination.write("manifests",row["rca"])
    report["report_ref"]=destination.write("manifests",report)
    return report
