from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from sentinelops.incidents.domain import IncidentConflict, IncidentWrite, check_transition


def command_fixture():
    payload = json.loads((Path(__file__).resolve().parents[1]/"samples/incident-evidence-record.sample.json").read_text())
    return IncidentWrite(payload,"OPEN",0,"fixture-key","trusted-test-producer","initial observation",{
        "availability":"ELIGIBLE","recovery_source":None,"source_refs":["fixture://source/one"],
        "config_refs":["config://incident/v1"],"trigger_sources":[{
            "source":"slo","signal_id":"opaque-bucket-one","emitted_at":payload["detected_at"],"source_ref":"fixture://slo/one"}]})


def test_structural_lifecycle_does_not_allow_skipping_verification_or_reopening_closed():
    command = command_fixture()
    check_transition(None,command)
    with pytest.raises(IncidentConflict,match="start OPEN"):
        check_transition(None,replace(command,lifecycle_stage="RCA_READY"))
    with pytest.raises(IncidentConflict,match="invalid lifecycle"):
        check_transition("OPEN",replace(command,lifecycle_stage="MITIGATION_PLANNED",expected_revision=1))
    with pytest.raises(IncidentConflict,match="closed recurrence"):
        check_transition("CLOSED",replace(command,expected_revision=9))
    check_transition("OPEN",replace(command,lifecycle_stage="RCA_READY",expected_revision=1),command.payload)


def test_detection_timestamp_and_expected_revision_are_not_silently_replaced():
    command = command_fixture()
    changed = deepcopy(command.payload);changed["detected_at"] = "2026-10-07T14:20:00Z"
    with pytest.raises(IncidentConflict,match="timestamp is immutable"):
        check_transition("OPEN",replace(command,payload=changed,expected_revision=1),command.payload)
    with pytest.raises(ValueError,match="nonnegative integer"):
        replace(command,expected_revision=True).validate()


def test_unknown_data_never_projects_recovered_and_recovery_source_is_required():
    command = command_fixture()
    payload = {**command.payload,"state":"recovered"}
    for context in ({**command.context,"availability":"UNKNOWN","recovery_source":"action"},command.context):
        with pytest.raises(ValueError,match="cannot project recovered"):
            replace(command,payload=payload,context=context,lifecycle_stage="RECOVERED").validate()
    valid = replace(command,payload=payload,lifecycle_stage="RECOVERED",expected_revision=5,
                    context={**command.context,"recovery_source":"natural"})
    check_transition("VERIFYING",valid)


def test_natural_recovery_still_requires_verification_without_inventing_an_action():
    command = command_fixture()
    verifying = replace(command,payload={**command.payload,"state":"recovering"},lifecycle_stage="VERIFYING",expected_revision=2)
    with pytest.raises(IncidentConflict,match="explicitly record natural recovery"):
        check_transition("OPEN",verifying)
    check_transition("OPEN",replace(verifying,context={**command.context,"recovery_source":"natural"}))
    with pytest.raises(IncidentConflict,match="invalid lifecycle"):
        check_transition("OPEN",replace(command,payload={**command.payload,"state":"recovered"},
            lifecycle_stage="RECOVERED",context={**command.context,"recovery_source":"natural"}))


def test_write_fingerprint_binds_actor_revision_and_context_but_not_retry_key():
    command = command_fixture()
    assert replace(command,idempotency_key="new-key").fingerprint() == command.fingerprint()
    for changed in (replace(command,actor="other"),replace(command,expected_revision=1),
                    replace(command,context={**command.context,"availability":"UNKNOWN"})):
        assert changed.fingerprint() != command.fingerprint()


def test_trigger_provenance_requires_unique_signals_and_source_references():
    command = command_fixture()
    for changes in ({"source_refs":[]},{"config_refs":["same","same"]},{"trigger_sources":[]},
                    {"trigger_sources":command.context["trigger_sources"]*2}):
        with pytest.raises(ValueError):
            replace(command,context={**command.context,**changes}).validate()
    payload = {**command.payload,"root_cause_ranking":[{"service":"rds","score":.9,"evidence_refs":["invented"]}]}
    with pytest.raises(ValueError,match="unknown or empty evidence"):
        replace(command,payload=payload).validate()


@pytest.mark.parametrize("context",[None,[],{"unexpected":"field"}])
def test_malformed_context_is_rejected_as_input_error(context):
    with pytest.raises(ValueError,match="invalid incident context"):
        replace(command_fixture(),context=context).validate()


@pytest.mark.parametrize("identifier",["slash/id","line\nbreak","x"*257,"nul\x00id"])
def test_stored_identifier_cannot_break_routes_or_headers(identifier):
    command=command_fixture()
    with pytest.raises(ValueError,match="URI-safe"):
        replace(command,payload={**command.payload,"incident_id":identifier}).validate()
