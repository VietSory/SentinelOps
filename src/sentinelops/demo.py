"""Explicit local-only Foundation fixtures; never inference or stored evidence."""
from datetime import datetime,timezone
from uuid import uuid4
from sentinelops.contracts import validate_payload

def demo_incident():
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "schema_version": "1.0",
        "incident_id": f"INC-{uuid4().hex[:10].upper()}",
        "detected_at": now,
        "state": "incident",
        "severity": "high",
        "affected_services": ["rds", "payment-service"],
        "service_signals": [
            {"service": "rds", "anomaly_score": 0.92, "degradation_risk": 0.86, "first_anomaly_at": now},
            {"service": "payment-service", "anomaly_score": 0.79, "degradation_risk": 0.71, "first_anomaly_at": now}
        ],
        "root_cause_ranking": [
            {"service": "rds", "score": 0.89, "evidence_refs": ["E-DEMO-1"]}
        ],
        "evidence": [
            {
                "id": "E-DEMO-1",
                "type": "metric",
                "service": "rds",
                "observed_at": now,
                "fact": "Demo evidence only; real RCA evidence is implemented in a later milestone.",
                "source_ref": "demo://foundation"
            }
        ]
    }
    validate_payload("incident-evidence-record.schema.json", payload)
    return payload
