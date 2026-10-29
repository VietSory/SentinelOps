from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
import yaml

from sentinelops.incidents.health import advance_health
from sentinelops.telemetry.core import BUSINESS_ROUTES, BucketMetrics, normalize_bucket, stable_id, timestamp
from sentinelops.telemetry.prometheus import SOURCE_PROFILE
from sentinelops.workers.incident_state import advance_groups, reference

POLICY = json.loads(Path("configs/slo_health_policy.json").read_text())
TOPOLOGY = yaml.safe_load(Path("configs/topology.yaml").read_text())
START = datetime(2026, 10, 29, tzinfo=timezone.utc)
PAY, CHECKOUT, INVENTORY = "payment-service", "checkout-service", "inventory-service"


def sequence(patterns):
    """Typed software fixtures; no raw/SQL/source authentication is implied."""
    identity = {"worker_version": "continuous-health/v1", "source_profile": SOURCE_PROFILE,
        "environment": "fixture", "policy_sha256": stable_id(POLICY), "prometheus_configuration_hash": "a" * 64,
        "endpoint_hash": "b" * 64, "artifact_root_hash": "c" * 64,
        "source_watermark_seconds": 30, "query_timeout_seconds": 1.}
    previous, prior_ref, result = {}, None, []
    for index, bad in enumerate(patterns):
        end = START + timedelta(minutes=index)
        evaluated = end + timedelta(seconds=31 + index % 7)
        objects, current = [], {}
        for service in sorted(BUSINESS_ROUTES):
            observation = None if bad is None else normalize_bucket(BucketMetrics(
                service, end, 120, 12 if service in bad else 0,
                ((0, 0), (100, 120), (float("inf"), 120)), 1., end + timedelta(seconds=30),
                (f"fixture://{service}/{index}",), source_profile=SOURCE_PROFILE), environment="fixture")
            signal = advance_health(previous.get(service), observation, service=service,
                environment="fixture", source_profile=SOURCE_PROFILE, bucket_end=end,
                evaluated_at=evaluated, policy=POLICY)
            current[service] = signal
            objects.append((reference("signals", signal), signal))
        batch = {"worker_version": "continuous-health/v1", "stream_id": stable_id(identity), "identity": identity,
                 "bucket_end": timestamp(end), "evaluated_at": timestamp(evaluated),
                 "previous_batch_ref": prior_ref, "signal_refs": sorted(ref for ref, _ in objects),
                 "current_raw_refs": [], "normalization_manifest_ref": "fixture://not-source-proof",
                 "source_status": "queried", "model_outputs_available": False, "actions_allowed": False}
        result.append((batch, objects))
        prior_ref, previous = reference("manifests", batch), current
    return result


def apply(state, item, *, topology=TOPOLOGY, policy=POLICY):
    return advance_groups(state, item[0], item[1], topology, policy)


def test_inclusive_late_sibling_root_is_seen_before_any_cascade_is_finalized():
    items = sequence([set(), {PAY, CHECKOUT}, {PAY, CHECKOUT},
                      {PAY, CHECKOUT, INVENTORY}, {PAY, CHECKOUT, INVENTORY}, set()])
    state = None
    for item in items[:4]:
        state, closed = apply(state, item)
        assert closed == []
    before = deepcopy(state)
    state, closed = apply(state, items[4])
    assert before["pending_openings"] and state["pending_openings"] == []
    assert len(closed) == 1
    report = closed[0]["correlation"]
    assert len(report["groups"]) == 3
    consumer = next(g for g in report["groups"] if g["affected_services"] == [CHECKOUT])
    assert consumer["grouping_reason"] == "ambiguous-shared-consumer"
    assert consumer["structural_candidates"] == [INVENTORY, PAY]
    assert {tuple(g["affected_services"]) for g in report["groups"]} == {(PAY,), (INVENTORY,), (CHECKOUT,)}
    assert report["as_of"] == timestamp(START + timedelta(minutes=4, seconds=35))
    assert report["future_count"] == 0 and report["historical_emissions_verified"] is False
    assert closed[0]["scope"]["closing_bucket_end"] == items[4][0]["bucket_end"]
    assert closed[0]["scope"]["closing_batch_ref"] == reference("manifests", items[4][0])
    assert closed[0]["scope_ref"] == reference("manifests", closed[0]["scope"])
    duplicate, emitted = apply(state, items[4])
    assert duplicate == state and emitted == []
    _, emitted = apply(state, items[5])
    assert emitted == []
    # Delivery order of the three atomic service rows cannot change final IDs.
    shuffled = deepcopy(items)
    for _, objects in shuffled:
        objects.reverse()
    other = None
    events = []
    for item in shuffled:
        other, emitted = apply(other, item)
        events.extend(emitted)
    assert events == closed


def test_onset_beyond_120_seconds_cannot_reopen_or_change_a_closed_group_identity():
    base = {PAY, CHECKOUT}
    items = sequence([set(), base, base, base, base | {INVENTORY},
                      base | {INVENTORY}, base | {INVENTORY}, base | {INVENTORY}])
    state, events = None, []
    for index, item in enumerate(items):
        state, emitted = apply(state, item)
        if index == 4:
            assert len(emitted) == 1
            assert emitted[0]["correlation"]["groups"][0]["affected_services"] == [CHECKOUT, PAY]
        events.extend(emitted)
    assert len(events) == 2
    assert events[1]["correlation"]["groups"][0]["affected_services"] == [INVENTORY]
    assert events[0]["scope_ref"] != events[1]["scope_ref"]
    assert events[0]["correlation"]["groups"][0]["structural_candidates"] == [PAY]


def test_no_input_progress_cannot_finalize_and_explicit_unknown_buckets_do_not_become_healthy():
    items = sequence([set(), {PAY, CHECKOUT}, {PAY, CHECKOUT}, None, None])
    state = None
    for item in items[:3]: state, closed = apply(state, item)
    retained = deepcopy(state)
    state, closed = apply(state, items[2])
    assert state == retained and closed == [] and len(state["pending_openings"]) == 2
    state, closed = apply(state, items[3])
    assert closed == []
    state, closed = apply(state, items[4])
    assert len(closed) == 1
    assert all(s["availability"] == "UNKNOWN" and s["health"] is None for s in state["service_states"].values())
    assert state["service_states"][PAY]["incident_active"] is True
    assert closed[0]["correlation"]["actions_allowed"] is False


@pytest.mark.parametrize("problem", ["gap", "scope", "reference", "missing", "topology", "policy", "rewrite"])
def test_changed_scope_corrupt_reference_gap_or_rewrite_cannot_advance_checkpoint(problem):
    items = sequence([set(), set(), set()])
    state, _ = apply(None, items[0])
    before = deepcopy(state)
    item, topology, policy = deepcopy(items[1]), deepcopy(TOPOLOGY), deepcopy(POLICY)
    if problem == "gap": item = deepcopy(items[2])
    elif problem == "scope":
        item[0]["identity"] = {**item[0]["identity"], "environment": "other"}
        item[0]["stream_id"] = stable_id(item[0]["identity"])
    elif problem == "reference": item[1][0] = ("artifact://signals/" + "d" * 64 + ".json", item[1][0][1])
    elif problem == "missing": item[1].pop()
    elif problem == "topology": topology["version"] += 1
    elif problem == "policy": policy["minimum_requests"] += 1
    elif problem == "rewrite":
        item = deepcopy(items[0])
        item[0]["source_status"] = "rewritten"
    with pytest.raises(ValueError): apply(state, item, topology=topology, policy=policy)
    assert state == before


@pytest.mark.parametrize("problem", ["duplicate", "future"])
def test_tampered_pending_openings_are_rejected_before_consuming_another_bucket(problem):
    items = sequence([set(), {PAY, CHECKOUT}, {PAY, CHECKOUT}, {PAY, CHECKOUT}])
    state = None
    for item in items[:3]: state, _ = apply(state, item)
    if problem == "duplicate":
        state["pending_openings"].append(deepcopy(state["pending_openings"][0]))
    else:
        item = state["pending_openings"][0]
        item["signal"]["evaluated_at"] = timestamp(START + timedelta(hours=1))
        item["signal"]["signal_id"] = stable_id({k: v for k, v in item["signal"].items() if k != "signal_id"})
        item["signal_ref"] = reference("signals", item["signal"])
    with pytest.raises(ValueError): apply(state, items[3])
