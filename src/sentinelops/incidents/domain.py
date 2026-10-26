"""Structural incident invariants. This is not a remediation authorization engine."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.telemetry.core import stable_id

TRANSITIONS = {
    "OPEN":{"RCA_READY","VERIFYING","ESCALATED"},
    "RCA_READY":{"EXPLAINED","MITIGATION_PLANNED","VERIFYING","ESCALATED"},
    "EXPLAINED":{"MITIGATION_PLANNED","VERIFYING","ESCALATED"},
    "MITIGATION_PLANNED":{"MITIGATING","ESCALATED"},
    "MITIGATING":{"VERIFYING","ESCALATED"},
    "VERIFYING":{"RECOVERED","ROLLED_BACK","ESCALATED"},
    "RECOVERED":{"CLOSED"},
    "ROLLED_BACK":{"ESCALATED"},
    "ESCALATED":{"CLOSED"},
    "CLOSED":set(),
}


class IncidentConflict(ValueError):
    """Expected revision or idempotency identity conflicts with durable state."""


class IncidentNotFound(LookupError):
    pass


@dataclass(frozen=True)
class IncidentWrite:
    payload: dict[str,Any]
    lifecycle_stage: str
    expected_revision: int
    idempotency_key: str
    actor: str
    reason: str
    context: dict[str,Any]

    def validate(self) -> None:
        validate_payload("incident-evidence-record.schema.json",self.payload)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}",self.payload["incident_id"]):
            raise ValueError("stored incident IDs must be URI-safe ASCII identifiers of at most 256 characters")
        if type(self.expected_revision) is not int or self.expected_revision < 0:
            raise ValueError("expected revision must be a nonnegative integer")
        if any(not isinstance(value,str) or not value.strip() or len(value)>256
               for value in (self.idempotency_key,self.actor,self.reason)):
            raise ValueError("idempotency key, actor and reason must be nonempty bounded strings")
        if self.lifecycle_stage not in TRANSITIONS:
            raise ValueError("unknown incident lifecycle stage")
        state = self.payload["state"]
        allowed = {"degrading","incident"}
        if self.lifecycle_stage in ("MITIGATING","VERIFYING"):
            allowed = {"recovering"}
        elif self.lifecycle_stage in ("RECOVERED",):
            allowed = {"recovered"}
        elif self.lifecycle_stage in ("ROLLED_BACK","ESCALATED"):
            allowed = {"escalated"}
        elif self.lifecycle_stage == "CLOSED":
            allowed = {"recovered","escalated"}
        if state not in allowed:
            raise ValueError("IER state disagrees with lifecycle projection")
        expected = {"availability","trigger_sources","source_refs","config_refs","recovery_source"}
        if not isinstance(self.context,dict) or set(self.context) != expected or self.context["availability"] not in ("ELIGIBLE","UNKNOWN"):
            raise ValueError("invalid incident context")
        if self.context["recovery_source"] not in (None,"natural","action","manual"):
            raise ValueError("invalid recovery source")
        for field in ("source_refs","config_refs"):
            values = self.context[field]
            if not isinstance(values,list) or not values or any(not isinstance(v,str) or not v.strip() for v in values) or len(set(values))!=len(values):
                raise ValueError("context references must be nonempty and unique")
        triggers = self.context["trigger_sources"]
        if not isinstance(triggers,list) or not triggers:
            raise ValueError("incident requires explicit trigger provenance")
        identities = set()
        for trigger in triggers:
            if not isinstance(trigger,dict) or set(trigger) != {"source","signal_id","emitted_at","source_ref"} or trigger["source"] not in ("anomaly","risk","slo","manual"):
                raise ValueError("invalid trigger provenance")
            if any(not isinstance(trigger[key],str) or not trigger[key].strip() for key in ("signal_id","source_ref")):
                raise ValueError("missing trigger identity/reference")
            if not isinstance(trigger["emitted_at"],str):
                raise ValueError("trigger emission time must be UTC text")
            utc_time(trigger["emitted_at"])
            identity = (trigger["source"],trigger["signal_id"])
            if identity in identities:
                raise ValueError("duplicate trigger signal identity")
            identities.add(identity)
        if state == "recovered" and (self.context["availability"] != "ELIGIBLE" or not self.context["recovery_source"]):
            raise ValueError("unknown data or unspecified recovery cannot project recovered")
        if len(json.dumps({"payload":self.payload,"context":self.context},allow_nan=False).encode()) > 1_000_000:
            raise ValueError("incident snapshot exceeds the 1MB limit; store raw evidence externally")

    def fingerprint(self) -> str:
        self.validate()
        return stable_id({"payload":self.payload,"lifecycle_stage":self.lifecycle_stage,
                          "expected_revision":self.expected_revision,"actor":self.actor,
                          "reason":self.reason,"context":self.context})


def check_transition(previous_stage: str | None, command: IncidentWrite, previous_payload: dict | None = None) -> None:
    command.validate()
    if previous_stage is None:
        if command.expected_revision != 0 or command.lifecycle_stage != "OPEN":
            raise IncidentConflict("new incidents must start OPEN at expected revision 0")
        return
    if previous_stage == "CLOSED" or (command.lifecycle_stage != previous_stage and command.lifecycle_stage not in TRANSITIONS[previous_stage]):
        raise IncidentConflict("invalid lifecycle transition; closed recurrence requires a new incident")
    if previous_stage in ("OPEN","RCA_READY","EXPLAINED") and command.lifecycle_stage == "VERIFYING" and command.context["recovery_source"] != "natural":
        raise IncidentConflict("verification without an action path must explicitly record natural recovery")
    if previous_payload and command.payload["detected_at"] != previous_payload["detected_at"]:
        raise IncidentConflict("the original incident detection timestamp is immutable")
