from copy import deepcopy
from datetime import timedelta
import json
import logging

import pytest

from sentinelops.incidents.rca_replay import group_severity
from sentinelops.incidents.severity import CONFIGS, load_policy
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore
from test_slo_health import END

PAYMENT = "payment-service"
CHECKOUT = "checkout-service"
INVENTORY = "inventory-service"


def bucket(index, latency=960, errors=0, eligible=True):
    return {"bucket_end": timestamp(END + timedelta(minutes=index)),
            "quality": {"eligible_for_inference": eligible},
            "payload": {"p95_latency_ms": latency, "error_rate": errors}}


def evidence(tmp_path, observations, *, roots=None):
    store = ArtifactStore(tmp_path)
    signals = {}
    for service, items in observations.items():
        refs = [store.write("observations", item) for item in items]
        signals[service] = ("fixture://signal/" + service, {"service": service, "evidence_refs": refs})
    group = {"affected_services": sorted(observations), "signal_ids": sorted(signals),
             "structural_candidates": [PAYMENT] if roots is None else roots}
    return store, group, signals


def classify(tmp_path, observations, *, roots=None):
    return group_severity(*evidence(tmp_path, observations, roots=roots))


@pytest.mark.parametrize("latency,expected", [
    (749.999, "medium"), (750, "medium"), (750.001, "low"),
    (1250, "low"), (1250.001, "medium"), (1500, "medium"), (1500.001, "medium")])
def test_non_checkout_latency_boundaries(tmp_path, latency, expected):
    assert classify(tmp_path, {PAYMENT: [bucket(0, latency), bucket(1, latency)]}) == expected


@pytest.mark.parametrize("errors,expected", [(0, "low"), (.049999, "low"), (.05, "low"),
                                            (.050001, "medium"), (.20, "medium")])
def test_non_checkout_error_boundary_never_establishes_critical(tmp_path, errors, expected):
    assert classify(tmp_path, {PAYMENT: [bucket(0, errors=errors), bucket(1, errors=errors)]}) == expected


@pytest.mark.parametrize("latency,errors,expected", [
    (750, 0, "low"), (1250, 0, "low"), (1500, 0, "low"), (1500.001, 0, "high"),
    (1030, .049999, "low"), (1030, .05, "high"), (1030, .050001, "high"),
    (1030, .199999, "high"), (1030, .20, "critical"), (1030, .200001, "critical")])
def test_checkout_boundaries_with_mild_payment(tmp_path, latency, errors, expected):
    observations = {PAYMENT: [bucket(0), bucket(1)],
                    CHECKOUT: [bucket(0, latency, errors), bucket(1, latency, errors)]}
    assert classify(tmp_path, observations) == expected


@pytest.mark.parametrize("items,expected", [
    ([bucket(0, errors=.2)], "medium"),
    ([bucket(0, errors=.2), bucket(2, errors=.2)], "medium"),
    ([bucket(0, errors=.2), bucket(1, errors=.199999)], "high"),
    ([bucket(0, errors=.2), bucket(1, errors=.2)], "critical"),
    ([bucket(1, errors=.2), bucket(0, errors=.2)], "critical"),
    ([bucket(0, errors=.2), bucket(0, errors=.2)], "medium"),
    ([bucket(0, errors=.2), bucket(1, errors=.2), bucket(2)], "medium"),
    ([bucket(0, 1501), bucket(2, 1501)], "medium"),
    ([bucket(0, 1501), bucket(1, 1500)], "medium"),
    ([bucket(0, errors=.05), bucket(1, 1501)], "high"),
])
def test_checkout_requires_two_distinct_adjacent_latest_buckets(tmp_path, items, expected):
    assert classify(tmp_path, {PAYMENT: [bucket(0), bucket(1)], CHECKOUT: items}) == expected


@pytest.mark.parametrize("service", [PAYMENT, CHECKOUT])
@pytest.mark.parametrize("items", [
    [], [bucket(0, eligible=False), bucket(1, eligible=False)],
    [bucket(0), bucket(1, eligible=False)],
    [bucket(0, errors=.2), bucket(1, errors=.2, eligible=False)],
    [bucket(0, 1600), bucket(1, 1600, eligible=False)],
    [bucket(0, None, None, False), bucket(1, None, None, False)],
])
def test_missing_or_ineligible_quality_cannot_establish_low_high_or_critical(tmp_path, service, items):
    observations = {PAYMENT: [bucket(0), bucket(1)], CHECKOUT: [bucket(0, 1030), bucket(1, 1030)]}
    observations[service] = items
    assert classify(tmp_path, observations) == "medium"


@pytest.mark.parametrize("roots,expected", [([PAYMENT], "low"), ([PAYMENT, PAYMENT], "low"),
                                           ([PAYMENT, INVENTORY], "high")])
def test_structural_roots_are_counted_instead_of_affected_services(tmp_path, roots, expected):
    observations = {PAYMENT: [bucket(0), bucket(1)], INVENTORY: [bucket(0), bucket(1)],
                    CHECKOUT: [bucket(0, 1030), bucket(1, 1030)]}
    assert classify(tmp_path, observations, roots=roots) == expected


def test_critical_takes_precedence_over_independent_roots(tmp_path):
    assert classify(tmp_path, {CHECKOUT: [bucket(0, errors=.2), bucket(1, errors=.2)]},
                    roots=[PAYMENT, INVENTORY]) == "critical"


def test_every_non_checkout_service_must_meet_low_and_use_its_maximum(tmp_path):
    observations = {PAYMENT: [bucket(0, 740), bucket(1, 1250)],
                    INVENTORY: [bucket(0), bucket(1, 1250.001)]}
    assert classify(tmp_path / "moderate", observations) == "medium"
    observations[INVENTORY][-1] = bucket(1, 1250)
    assert classify(tmp_path / "mild", observations) == "low"
    observations[INVENTORY][0] = bucket(0, errors=.050001)
    assert classify(tmp_path / "errors", observations) == "medium"


def test_checkout_only_below_high_is_low_but_empty_group_is_medium(tmp_path):
    assert classify(tmp_path / "checkout", {CHECKOUT: [bucket(0, 1030), bucket(1, 1030)]}) == "low"
    assert classify(tmp_path / "empty", {}) == "medium"


def test_demo_uses_the_same_implementation_for_live_and_replay_without_mutation(tmp_path):
    from sentinelops.workers.incident_journal import group_severity as live_severity

    args = evidence(tmp_path, {PAYMENT: [bucket(0, 960), bucket(1, 960)],
                               CHECKOUT: [bucket(0, 1030), bucket(1, 1030)]})
    before = deepcopy(args[1:])
    assert live_severity is group_severity
    assert group_severity(*args) == live_severity(*args) == "low"
    assert args[1:] == before


def test_policy_is_cached_and_hash_covers_severity_and_referenced_slo_thresholds(tmp_path, caplog, monkeypatch):
    policy = load_policy()
    severity = json.loads((CONFIGS / "severity_policy.json").read_text())
    health = json.loads((CONFIGS / "slo_health_policy.json").read_text())
    assert policy.sha256 == stable_id({"severity_policy": severity, "slo_health_policy": health})
    assert policy.version == "severity/v2"
    assert policy.latency_min_ms == health["latency_ms"] and policy.error_rate == health["error_rate"]
    monkeypatch.setattr("sentinelops.incidents.severity.CONFIGS", tmp_path / "must-not-reload")
    assert load_policy() is policy
    with caplog.at_level(logging.INFO, logger="sentinelops.incidents.severity"):
        assert classify(tmp_path / "evidence", {PAYMENT: [bucket(0), bucket(1)]}) == "low"
    assert "severity_policy_sha256=" + policy.sha256 in caplog.text


def test_installed_worker_loads_deployed_configs_from_working_directory(tmp_path, monkeypatch):
    expected = load_policy()
    runtime = tmp_path / "runtime"
    (runtime / "configs").mkdir(parents=True)
    for name in ("severity_policy.json", "slo_health_policy.json"):
        (runtime / "configs" / name).write_bytes((CONFIGS / name).read_bytes())
    load_policy.cache_clear()
    try:
        with monkeypatch.context() as patch:
            patch.setattr("sentinelops.incidents.severity.CONFIGS", tmp_path / "site-packages/configs")
            patch.chdir(runtime)
            assert load_policy() == expected
    finally:
        load_policy.cache_clear()
