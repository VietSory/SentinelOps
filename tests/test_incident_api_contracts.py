from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator,FormatChecker
import pytest
from referencing.exceptions import NoSuchResource

from sentinelops.contracts import load_schema,validate_payload
from sentinelops.incidents.validation import validate_wrapper
from sentinelops.schema_registry import CONTRACT_REGISTRY,retrieve_contract

ROOT=Path(__file__).resolve().parents[1]


def sample(name):
    return json.loads((ROOT/"samples"/(name+".sample.json")).read_text())


@pytest.mark.parametrize("name",["incident-write","incident-envelope","incident-list","api-error"])
def test_supplemental_samples_resolve_local_contracts_and_validate_semantics(name):
    payload=sample(name)
    Draft202012Validator(load_schema(name+".schema.json"),format_checker=FormatChecker(),registry=CONTRACT_REGISTRY).validate(payload)
    validate_wrapper(name+".schema.json",payload)
    validate_payload(name+".schema.json",payload)


@pytest.mark.parametrize("uri",["https://example.com/schema.json","file:///etc/passwd",
    "https://sentinelops.local/contracts/../../secret.schema.json","https://sentinelops.local/contracts/unknown.schema.json"])
def test_schema_registry_rejects_remote_file_and_unknown_references(uri):
    with pytest.raises(NoSuchResource):
        retrieve_contract(uri)


@pytest.mark.parametrize("mutation",["identity","audit_revision","duplicate_event","cursor","invented_evidence","future_trigger"])
def test_envelope_cannot_detach_from_its_ier_or_audit(mutation):
    payload=sample("incident-envelope")
    if mutation=="identity": payload["incident_id"]="other"
    elif mutation=="audit_revision": payload["timeline"]["events"][0]["revision"]=2
    elif mutation=="duplicate_event": payload["timeline"]["events"]*=2
    elif mutation=="cursor": payload["timeline"]["next_after_revision"]=1
    elif mutation=="invented_evidence": payload["payload"]["root_cause_ranking"][0]["evidence_refs"]=["not-real"]
    else: payload["context"]["trigger_sources"][0]["emitted_at"]="2099-01-01T00:00:00Z"
    with pytest.raises(ValueError):
        validate_wrapper("incident-envelope.schema.json",payload)
    with pytest.raises(ValueError):
        validate_payload("incident-envelope.schema.json",payload)


def test_nested_frozen_ier_shape_and_context_additional_properties_are_strict():
    for field in ("payload","context"):
        payload=sample("incident-write");payload[field]["invented"]=True
        with pytest.raises(Exception) as error:
            Draft202012Validator(load_schema("incident-write.schema.json"),registry=CONTRACT_REGISTRY).validate(payload)
        assert "Additional properties" in str(error.value) or "Unevaluated properties" in str(error.value)
