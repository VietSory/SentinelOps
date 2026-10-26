"""Semantic checks for supplemental contracts; legacy IER stays authoritative."""
from sentinelops.contracts import utc_time,validate_payload
from sentinelops.incidents.domain import IncidentWrite


def validate_wrapper(name: str,payload: dict) -> None:
    if name in ("incident-write.schema.json","incident-envelope.schema.json"):
        context = payload["context"]
        stage = payload["lifecycle_stage"] if name=="incident-write.schema.json" else context["lifecycle_stage"]
        provenance = {key:value for key,value in context.items() if key!="lifecycle_stage"}
        IncidentWrite(payload["payload"],stage,1,"schema-validation",payload.get("actor","schema-validator"),payload["reason"],provenance).validate()
        if name=="incident-envelope.schema.json":
            if payload["incident_id"]!=payload["payload"]["incident_id"]:
                raise ValueError("incident envelope identity differs from IER")
            recorded = utc_time(payload["recorded_at"])
            if utc_time(payload["payload"]["detected_at"])>recorded:
                raise ValueError("incident detection cannot follow its recorded revision")
            if any(utc_time(trigger["emitted_at"])>recorded for trigger in provenance["trigger_sources"]):
                raise ValueError("trigger emission cannot follow its recorded revision")
            events = payload["timeline"]["events"]
            previous,identities = 0,set()
            for event in events:
                utc_time(event["recorded_at"])
                if event["incident_id"]!=payload["incident_id"] or not previous<event["revision"]<=payload["revision"]:
                    raise ValueError("timeline identity/revision ordering differs from envelope")
                if event["event_id"] in identities:
                    raise ValueError("duplicate timeline event identity")
                previous = event["revision"];identities.add(event["event_id"])
            cursor = payload["timeline"]["next_after_revision"]
            if cursor is not None and (not events or cursor!=previous or cursor>=payload["revision"]):
                raise ValueError("timeline continuation does not match the final event")
            if events and events[-1]["revision"]==payload["revision"]:
                event = events[-1]
                if (event["actor"],event["reason"],event["lifecycle_stage"])!=(payload["actor"],payload["reason"],stage):
                    raise ValueError("current audit event differs from revision metadata")
    if name=="incident-list.schema.json":
        seen = set()
        for item in payload["items"]:
            utc_time(item["detected_at"])
            if item["incident_id"] in seen:
                raise ValueError("duplicate incident list identity")
            seen.add(item["incident_id"])
