"""Matching contract and fail-closed decisions; entirely offline fixtures."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sentinelops.contracts import load_schema, validate_payload
from sentinelops.remediation import matching
from sentinelops.remediation.matching import decide, extract_symptoms, fingerprint, load_catalog, rank, score
from sentinelops.schema_registry import CONTRACT_REGISTRY, PREFIX
from sentinelops.telemetry.core import stable_id

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "configs/runbook_catalog.json"


@pytest.fixture
def catalog():
    return load_catalog(CATALOG)


def inputs():
    return {
        "severity": "low",
        "rca": {
            "abstained": False, "ranking": ["payment-service"], "group_id": "payment-opening",
            "correlation": {"groups": [{"group_id": "payment-opening",
                "affected_services": ["payment-service", "checkout-service"],
                "first_onset_at": "2026-11-18T00:05:00Z"}]},
            "candidates": [
                {"service": "payment-service", "components": {"recent_change_support": {"value": 1.0}}},
                {"service": "rds", "components": {"log_support": {"value": 0.0},
                                                   "anomaly_strength": {"value": None}}},
            ],
        },
    }, {"payment-service": [{"p95_latency_ms": 1000.0, "error_rate": 0.01}]}


def symptoms(**changes):
    derived, observations = inputs()
    return {**extract_symptoms(derived=derived, observations=observations), **changes}


def model_signal(state="watch", *, service="payment-service", emitted="2026-11-18T00:04:01Z"):
    return ["artifact://fixture/model", {"service": service, "status": "AVAILABLE", "evaluated_at": emitted,
        "inference": {"risk": {"schema_version": "1.0", "service": service,
            "observed_at": "2026-11-18T00:04:00Z", "risk_score": .6, "state": state,
            "model_name": "xgboost", "model_version": "fixture/v1"}}}]


def test_demo_auto_and_complete_audit_record(catalog):
    s = symptoms()
    before = deepcopy((s, catalog))
    result = decide(s, catalog)
    assert result["decision"] == "oncall"
    assert result["score"] >= .80 and result["margin"] >= .15
    assert result["runbook_id"] == "payment-bad-deploy-rollback"
    assert result["executor_runbook"] == "payment-rollback-known-good/v1"
    assert result["decision_version"] == "runbook-decision/v1"
    assert result["catalog_sha256"] == stable_id(catalog)
    assert result["ranking"] == rank(s, catalog)[:3]
    assert result["fingerprint"] == fingerprint(s, result["runbook_id"])
    assert len(result["ranking"]) == 3
    assert (s, catalog) == before
    result["symptoms"]["affected_services"].clear()
    assert s["affected_services"]


def test_five_catalog_entries_and_single_allowlisted_executor(catalog):
    assert {e["id"] for e in catalog["entries"]} == {
        "payment-bad-deploy-rollback", "inventory-bad-deploy", "payment-errors-no-change",
        "db-lock-contention", "checkout-latency-downstream"}
    executable = [e for e in catalog["entries"] if e["executable"]]
    assert len(executable) == 1 and executable[0]["allowed_severities"] == ["low"]
    assert all(e["executor_runbook"] is None for e in catalog["entries"] if not e["executable"])
    assert all(any(ord(char) > 127 for char in " ".join(e["manual_steps"])) for e in catalog["entries"])


def test_5xx_without_change_selects_manual_but_medium_routes_engineer(catalog):
    derived, observations = inputs()
    derived["severity"] = "medium"
    derived["rca"]["candidates"][0]["components"]["recent_change_support"]["value"] = 0.0
    observations["payment-service"][0].update(p95_latency_ms=400.0, error_rate=.12)
    s = extract_symptoms(derived=derived, observations=observations)
    result = decide(s, catalog)
    assert s["modality"] == "errors" and s["deployment_change"] is False
    assert result["runbook_id"] == "payment-errors-no-change" and result["score"] == 1.0
    assert result["decision"] == "engineer" and result["executor_runbook"] is None
    assert result["reasons"] == ["severity-not-supported"]
    assert decide({**s, "severity": "low"}, catalog)["decision"] == "engineer"


def test_unknown_symptoms_do_not_pick_a_runbook(catalog):
    result = decide(symptoms(root_service="unfamiliar-service", deployment_change=False,
                             checkout_impacted=False, affected_services=["unfamiliar-service"]), catalog)
    assert result["decision"] == "engineer" and result["runbook_id"] is None


@pytest.mark.parametrize("severity", ["medium", "high", "critical"])
def test_high_match_never_overrides_severity(catalog, severity):
    result = decide(symptoms(severity=severity), catalog)
    assert result["score"] == 1.0 and result["decision"] == "engineer"
    assert result["reasons"] == ["severity-not-supported"]


def test_abstention_precedes_severity_and_preserves_null_root(catalog):
    derived, _ = inputs()
    derived["severity"] = "medium"
    derived["rca"].update(abstained=True, ranking=[])
    s = extract_symptoms(derived=derived, observations={})
    assert s["root_service"] is None and s["root_p95_ms"] is None and s["root_error_rate"] is None
    assert s["modality"] == "none" and s["deployment_change"] is False
    assert decide(s, catalog)["reasons"] == ["rca-abstained"]
    assert decide(s, catalog)["decision"] == "engineer"


def test_two_close_matches_prevent_auto_and_ties_sort_by_id(catalog):
    competing = deepcopy(catalog["entries"][0])
    competing.update(id="nearby-manual", executable=False, executor_runbook=None)
    catalog["entries"].append(competing)
    result = decide(symptoms(), catalog)
    assert result["decision"] == "engineer" and result["margin"] == 0.0
    assert "insufficient-margin" in result["reasons"]
    assert result["ranking"][0]["id"] == "nearby-manual"
    catalog["entries"].reverse()
    reordered = decide(symptoms(), catalog)
    assert reordered["ranking"] == result["ranking"]
    assert reordered["decision"] == result["decision"]
    assert reordered["fingerprint"] == result["fingerprint"]
    assert reordered["catalog_sha256"] == stable_id(catalog)


def test_single_entry_margin_is_its_score(catalog):
    catalog["entries"] = catalog["entries"][:1]
    result = decide(symptoms(), catalog)
    assert result["margin"] == result["score"] == 1.0
    assert result["decision"] == "oncall"


def test_margin_threshold_is_configurable(catalog):
    competing = deepcopy(catalog["entries"][0])
    competing.update(id="second-manual", executable=False, executor_runbook=None)
    competing["features"] = [{"field": "modality", "equals": "latency", "weight": .875},
                             {"field": "db_lock", "equals": True, "weight": .125}]
    catalog["entries"].append(competing)
    result = decide(symptoms(), catalog)
    assert result["margin"] == .125 and result["decision"] == "engineer"
    catalog["thresholds"]["auto_min_margin"] = .125
    assert decide(symptoms(), catalog)["decision"] == "engineer"


@pytest.mark.parametrize('top,margin,expected', [
    (.79, .20, 'engineer'), (.80, .15, 'oncall'), (.80, .149, 'engineer'),
    (1., .50, 'oncall'), (.50, .50, 'engineer'),
])
def test_approval_threshold_boundaries_never_auto(catalog, monkeypatch, top, margin, expected):
    monkeypatch.setattr(matching, 'rank', lambda *args: [
        {'id': catalog['entries'][0]['id'], 'score': top},
        {'id': catalog['entries'][1]['id'], 'score': top - margin}])
    result = decide(symptoms(), catalog)
    assert result['decision'] == expected
    assert result['decision'] != 'auto'


def test_lower_catalog_thresholds_cannot_restore_automatic_or_weak_match(catalog):
    catalog['thresholds'].update(auto_min_score=.50, auto_min_margin=0.)
    assert decide(symptoms(onset='gradual', db_lock=True), catalog)['decision'] == 'engineer'


def test_weighted_score_required_gates_and_type_sensitive_equality(catalog):
    entry = catalog["entries"][0]
    result = score(symptoms(onset="gradual", db_lock=True), entry)
    assert result["score"] == pytest.approx(.65)
    assert result["matched"] == ["checkout_impacted", "latency_band", "modality"]
    assert score(symptoms(deployment_change=False), entry)["score"] == 0.0
    assert score(symptoms(deployment_change=1), entry)["required_failed"] == ["deployment_change"]
    missing = symptoms()
    del missing["deployment_change"]
    assert score(missing, entry)["required_failed"] == ["deployment_change"]


@pytest.mark.parametrize("field", sorted(matching.FIELDS))
def test_missing_symptom_always_routes_engineer(catalog, field):
    s = symptoms()
    del s[field]
    assert decide(s, catalog)["decision"] == "engineer"


@pytest.mark.parametrize("changes", [
    {"severity": "UNKNOWN"}, {"rca_abstained": "false"}, {"deployment_change": 1},
    {"root_p95_ms": float("nan")}, {"root_error_rate": float("inf")}, {"root_error_rate": -1},
    {"root_error_rate": 1.1}, {"root_p95_ms": True}, {"root_service": None},
    {"affected_services": []}, {"affected_services": ["payment-service", "checkout-service"]},
])
def test_invalid_symptoms_fail_closed(catalog, changes):
    assert decide(symptoms(**changes), catalog)["decision"] == "engineer"


@pytest.mark.parametrize("p95,error,modality,band", [
    (750, .05, "none", "none"), (750.1, .05, "latency", "mild"),
    (1250, 0, "latency", "mild"), (1250.1, 0, "latency", "moderate"),
    (1500, 0, "latency", "moderate"), (1500.1, .051, "both", "severe"),
    (300, .051, "errors", "none"),
])
def test_extract_threshold_boundaries(p95, error, modality, band):
    derived, observations = inputs()
    observations["payment-service"][0].update(p95_latency_ms=p95, error_rate=error)
    s = extract_symptoms(derived=derived, observations=observations)
    assert (s["modality"], s["latency_band"]) == (modality, band)
    assert type(s["root_p95_ms"]) is type(s["root_error_rate"]) is float
    assert s["affected_services"] == ["checkout-service", "payment-service"]


def test_thresholds_come_from_existing_health_policy(tmp_path, monkeypatch):
    policy = json.loads(matching.HEALTH_POLICY.read_text())
    policy.update(latency_ms=1100.0, error_rate=.15)
    path = tmp_path / "health.json"
    path.write_text(json.dumps(policy))
    monkeypatch.setattr(matching, "HEALTH_POLICY", path)
    derived, observations = inputs()
    assert extract_symptoms(derived=derived, observations=observations)["modality"] == "none"


def test_eligible_envelopes_only_and_maxima_across_buckets():
    derived, _ = inputs()
    observations = {"payment-service": [
        {"quality": {"eligible_for_inference": False}, "payload": None},
        {"quality": {"eligible_for_inference": True}, "payload": {"p95_latency_ms": 900, "error_rate": .1}},
        {"quality": {"eligible_for_inference": True}, "payload": {"p95_latency_ms": 1200, "error_rate": .01}},
    ]}
    s = extract_symptoms(derived=derived, observations=observations)
    assert (s["root_p95_ms"], s["root_error_rate"], s["modality"]) == (1200.0, .1, "both")


@pytest.mark.parametrize("bad", [None, [], [{"quality": {"eligible_for_inference": False}, "payload": None}],
    [{"p95_latency_ms": None, "error_rate": .01}], [{"p95_latency_ms": 1000, "error_rate": float("nan")}],
    [{"p95_latency_ms": 1000, "error_rate": .01, "service": "inventory-service"}]])
def test_missing_or_invalid_root_evidence_raises_value_error(bad):
    derived, _ = inputs()
    with pytest.raises(ValueError):
        extract_symptoms(derived=derived, observations={"payment-service": bad})


@pytest.mark.parametrize("state,emitted,expected", [
    ("watch", "2026-11-18T00:04:01Z", "gradual"),
    ("degrading", "2026-11-18T00:04:01Z", "gradual"),
    ("normal", "2026-11-18T00:04:01Z", "abrupt"),
    ("watch", "2026-11-18T00:05:00Z", "unknown"),
])
def test_onset_uses_only_pre_onset_available_root_risk(state, emitted, expected):
    derived, observations = inputs()
    derived["model_signals"] = [model_signal(state, emitted=emitted)]
    assert extract_symptoms(derived=derived, observations=observations)["onset"] == expected
    derived["model_signals"] = [model_signal(service="inventory-service")]
    assert extract_symptoms(derived=derived, observations=observations)["onset"] == "unknown"


def test_db_support_and_deployment_come_from_candidates_not_optional_change_flag():
    derived, observations = inputs()
    derived["deployment_change"] = False
    derived["rca"]["candidates"][1]["components"]["anomaly_strength"]["value"] = .1
    s = extract_symptoms(derived=derived, observations=observations)
    assert s["deployment_change"] is True and s["db_lock"] is True


@pytest.mark.parametrize("field", ["root_service", "modality", "latency_band", "deployment_change", "checkout_impacted"])
def test_fingerprint_is_stable_but_changes_with_each_identity_field(field):
    s = symptoms()
    expected = fingerprint(s, "payment-bad-deploy-rollback")
    assert expected == fingerprint(dict(reversed(list(s.items()))), "payment-bad-deploy-rollback")
    assert expected == fingerprint({**s, "severity": "medium", "root_p95_ms": 1100}, "payment-bad-deploy-rollback")
    changed = {"root_service": "inventory-service", "modality": "both", "latency_band": "severe",
               "deployment_change": False, "checkout_impacted": False}
    assert expected != fingerprint({**s, field: changed[field]}, "payment-bad-deploy-rollback")
    assert expected != fingerprint(s, None)
    assert len(expected) == 64


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(extra=True),
    lambda c: c["thresholds"].update(extra=True),
    lambda c: c["thresholds"].update(auto_min_score=1.1),
    lambda c: c["thresholds"].update(auto_min_score=.4),
    lambda c: c["entries"][0].update(extra=True),
    lambda c: c["entries"][0].update(executor_runbook="arbitrary-shell/v1"),
    lambda c: c["entries"][0].update(executor_runbook=None),
    lambda c: c["entries"][1].update(executor_runbook="payment-rollback-known-good/v1"),
    lambda c: c["entries"][1].update(id=c["entries"][0]["id"]),
    lambda c: c["entries"][0]["required"].update(invented=False),
    lambda c: c["entries"][0]["required"].update(deployment_change=1),
    lambda c: c["entries"][0]["features"][0].update(weight=0),
    lambda c: c["entries"][0]["features"][0].update(weight=float("nan")),
    lambda c: c["entries"][0]["features"][0].update(field="unknown"),
    lambda c: c["entries"][0]["features"][0].update(equals="UNKNOWN"),
    lambda c: c["entries"][0]["features"][0].update(extra=True),
    lambda c: c["entries"][0]["features"][0].update(**{"in": ["latency"]}),
    lambda c: c["entries"][0]["features"].append(deepcopy(c["entries"][0]["features"][0])),
])
def test_invalid_catalog_rejected(tmp_path, catalog, mutation):
    mutation(catalog)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalog(path)


def test_catalog_loading_errors_and_isolation(tmp_path):
    with pytest.raises(ValueError):
        load_catalog(tmp_path / "missing.json")
    path = tmp_path / "invalid.json"
    path.write_text("{")
    with pytest.raises(ValueError):
        load_catalog(path)
    first = load_catalog(CATALOG)
    first["entries"].clear()
    assert len(load_catalog(CATALOG)["entries"]) == 5


def test_schema_discovered_by_existing_registry(catalog):
    schema = load_schema("runbook-catalog.schema.json")
    Draft202012Validator.check_schema(schema)
    validate_payload("runbook-catalog.schema.json", catalog)
    resource = CONTRACT_REGISTRY.get_or_retrieve(PREFIX + "runbook-catalog.schema.json").value
    assert resource.contents == schema


def test_extraction_with_existing_verified_rca_fixture():
    from test_rca import evaluate
    s = extract_symptoms(derived={"rca": evaluate(), "severity": "low"},
        observations={"payment-service": [{"p95_latency_ms": 1000, "error_rate": .01}]})
    assert s["root_service"] == "payment-service" and s["deployment_change"] is True
