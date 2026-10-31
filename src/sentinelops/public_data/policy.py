"""Enforce the owner-approved source roles before data/model admission."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sentinelops.contracts import validate_payload


DEFAULT_POLICY = Path(__file__).resolve().parents[3] / "configs/training_data_contract.json"
CURRENT_POLICY = DEFAULT_POLICY.with_name("training_data_contract_v2.json")


def load_policy(path: Path = DEFAULT_POLICY) -> dict:
    """Historical v1 authority for immutable GAMMA source reproduction."""
    policy = json.loads(path.read_text(encoding="utf-8"))
    validate_payload("training-data-policy.schema.json", policy)
    return policy


def load_training_policy(path: Path = CURRENT_POLICY) -> dict:
    """Current owner-approved prospective fitting authority (ADR-014)."""
    policy = json.loads(path.read_text(encoding="utf-8"))
    validate_payload("training-data-policy-v2.schema.json", policy)
    return policy


def require_current_training_source(dataset: str, policy: dict) -> None:
    validate_payload("training-data-policy-v2.schema.json", policy)
    source = policy["sources"].get(dataset)
    if source is None or source.get("role") != "primary-training":
        raise ValueError("training-data/v2 permits prospective registered local HTTP fitting only")


def policy_hash(policy: dict) -> str:
    schema = "training-data-policy-v2.schema.json" if policy.get("policy_version") == "training-data/v2" else "training-data-policy.schema.json"
    validate_payload(schema, policy)
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def require_training_source(dataset: str, policy: dict) -> None:
    validate_payload("training-data-policy.schema.json", policy)
    source = policy["sources"].get(dataset.lower())
    if source is None or source.get("role") != "primary-training":
        raise ValueError("frozen training-data/v1 permits ML fitting on GAMMA only")


def require_historical_smd(machine: str, *, acknowledged: bool) -> None:
    if not acknowledged or machine != "machine-1-1":
        raise ValueError("SMD is excluded from v1; only explicit historical machine-1-1 reproduction is allowed")
