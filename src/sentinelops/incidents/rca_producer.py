"""Trusted local replay producer; verified raw lineage precedes every DB write.

This does not emit historical alerts, run continuously or authorize actions.
An immutable intent fixes the actual replay emission clock before retries.
"""
from __future__ import annotations

from copy import deepcopy
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from sentinelops.contracts import utc_time
from sentinelops.incidents.domain import IncidentWrite
from sentinelops.incidents.rca import validate_policy
from sentinelops.incidents.rca_replay import replay_rca
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore


def verify_rca_report(store: ArtifactStore, reference: str, *, policy: dict) -> dict:
    """Recompute facts, support and ranking, not merely a client-supplied score."""
    validate_policy(policy)
    report = store.read(reference)
    if report.get("report_version") != "local-evidence-rca-replay/v1" or report.get("policy") != policy:
        raise ValueError("RCA report version/policy differs from the producer's selected policy")
    with TemporaryDirectory(prefix="sentinelops-rca-producer-") as temporary:
        rebuilt = replay_rca(store, store, store, report["source_correlation_ref"], policy,
                             Path(temporary)/"verified", as_of=utc_time(report["as_of"]))
    def body(value):
        return {key:item for key,item in value.items() if key not in ("source_commit","report_ref")}
    if body(report) != body(rebuilt):
        raise ValueError("RCA report does not reproduce from archived raw evidence")
    for row in report["results"]:
        if store.read(row["rca_ref"]) != row["rca"]:
            raise ValueError("standalone RCA detail differs from the verified report")
    return report


def prepare_intent(store: ArtifactStore, report_ref: str, *, policy: dict) -> str:
    report = verify_rca_report(store, report_ref, policy=policy)
    emitted = datetime.now(timezone.utc)
    if emitted < utc_time(report["as_of"]):
        raise ValueError("cannot emit a replay before its analytical cutoff")
    return store.write("manifests", {"intent_version":"local-rca-producer-intent/v1",
        "mode":"event-time-analytical", "report_ref":report_ref, "emitted_at":timestamp(emitted)})


def verified_intent(store: ArtifactStore, intent_ref: str, *, policy: dict) -> tuple[dict,dict]:
    intent = store.read(intent_ref)
    if (set(intent) != {"intent_version","mode","report_ref","emitted_at"}
        or intent["intent_version"] != "local-rca-producer-intent/v1" or intent["mode"] != "event-time-analytical"):
        raise ValueError("invalid local RCA producer intent")
    report = verify_rca_report(store, intent["report_ref"], policy=policy)
    if utc_time(intent["emitted_at"]) < utc_time(report["as_of"]):
        raise ValueError("producer emission cannot be backdated to before its analytical cutoff")
    return intent,report


def project_commands(intent_ref: str, intent: dict, report: dict) -> list[list[IncidentWrite]]:
    """Legacy IER projection; only called with a verified local intent/report."""
    groups = []
    for row in report["results"]:
        result = row["rca"]
        group = next(g for g in result["correlation"]["groups"] if g["group_id"] == row["group_id"])
        identifier = "RCA-REPLAY-"+stable_id({"intent_ref":intent_ref,"group_id":row["group_id"]})
        evidence = deepcopy(result["evidence"])
        detail = {"type":"timeline", "service":None, "observed_at":report["as_of"],
            "source_ref":row["rca_ref"], "fact":
            "Event-time analytical RCA; ranked hypotheses are not confirmed causes; "
            "historical emission unavailable; actions_allowed=false; abstention="+
            ",".join(result["abstention_reasons"])}
        detail["id"] = stable_id(detail)
        evidence.append(detail)
        emission = {"type":"timeline", "service":None, "observed_at":intent["emitted_at"],
            "source_ref":intent_ref, "fact":"Actual local replay emission clock; not the historical incident detection time"}
        emission["id"] = stable_id(emission)
        evidence.append(emission)
        candidates = {c["service"]:c for c in result["candidates"]}
        ranking = [{key:candidates[service][key] for key in ("service","score","evidence_refs")}
                   for service in result["ranking"]]
        payload = {"schema_version":"1.0", "incident_id":identifier, "detected_at":intent["emitted_at"],
            "state":"incident", "severity":row["severity"], "affected_services":group["affected_services"],
            "service_signals":[], "root_cause_ranking":[], "evidence":evidence}
        # SLO provenance is explicit; no fake IF score fills legacy required fields.
        context = {"availability":"UNKNOWN", "recovery_source":None,
            "source_refs":[intent_ref,intent["report_ref"],row["rca_ref"],report["source_health_ref"]],
            "config_refs":["config://rca-policy/"+report["policy_sha256"]],
            "trigger_sources":[{"source":"slo", "signal_id":signal_id,
                "emitted_at":intent["emitted_at"], "source_ref":report["source_health_ref"]}
                for signal_id in group["signal_ids"]]}
        commands = []
        for revision,stage in enumerate(("OPEN","RCA_READY","ESCALATED")):
            current = deepcopy(payload)
            if revision: current["root_cause_ranking"] = ranking
            if stage == "ESCALATED": current["state"] = "escalated"
            reason = {"OPEN":"local analytical replay of verified SLO openings",
                "RCA_READY":"source-verified RCA hypotheses; consult immutable detail for abstention",
                "ESCALATED":"analytical replay is not action eligible; no recovery or remediation asserted"}[stage]
            command = IncidentWrite(current,stage,revision,identifier+":"+stage,
                                    "local-rca-replay-producer",reason,deepcopy(context))
            command.validate()
            commands.append(command)
        groups.append(commands)
    return groups


async def publish_intent(repository, store: ArtifactStore, intent_ref: str, *, policy: dict) -> dict:
    intent,report = await asyncio.to_thread(verified_intent,store,intent_ref,policy=policy)
    commands = project_commands(intent_ref,intent,report)
    await repository.check_ready()
    async with repository.pool.acquire() as connection:
        now = await connection.fetchval("SELECT clock_timestamp()")
    if utc_time(intent["emitted_at"]) > now:
        raise ValueError("producer emission clock is ahead of the control database")
    published = []
    for group in commands:
        # Stable keys return original revisions after a crash at any step.
        for command in group:
            stored = await repository.write(command)
        published.append({"incident_id":stored["incident_id"], "revision":stored["revision"],
                          "payload":stored["payload"], "context":stored["context"]})
    return {"producer_version":"local-rca-replay-producer/v1", "intent_ref":intent_ref,
        "report_ref":intent["report_ref"], "emitted_at":intent["emitted_at"],
        "incidents":published, "actions_allowed":False, "historical_emissions_verified":False}
