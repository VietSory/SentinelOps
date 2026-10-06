from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from .contracts import validate_payload

app = FastAPI(
    title="SentinelOps API",
    version="0.1.0",
    description="Foundation API skeleton. Model/RCA/remediation logic is intentionally mocked until their milestones are implemented.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "sentinelops-api", "version": "0.1.0"}


@app.post("/v1/detect")
def detect(feature_window: dict[str, Any]) -> dict[str, Any]:
    try:
        validate_payload("feature-window.schema.json", feature_window)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Skeleton response. Replace only the internal implementation in the ML milestone;
    # the response contract must remain stable unless an ADR approves a breaking change.
    response = {
        "schema_version": "1.0",
        "service": feature_window["service"],
        "observed_at": feature_window["window_end"],
        "anomaly_score": 0.50,
        "is_anomalous": False,
        "model_name": "isolation-forest",
        "model_version": "skeleton-v0",
    }
    validate_payload("detection-result.schema.json", response)
    return response


@app.post("/v1/incidents")
def create_incident(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Foundation endpoint: accepts a complete Incident Evidence Record so the team can
    integrate downstream consumers before correlation/RCA implementation is finished.
    """
    try:
        validate_payload("incident-evidence-record.schema.json", payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return payload


@app.post("/v1/incidents/demo")
def create_demo_incident() -> dict[str, Any]:
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
