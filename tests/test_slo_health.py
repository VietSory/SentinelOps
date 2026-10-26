from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

import pytest

from sentinelops.contracts import validate_payload
from sentinelops.incidents.health import advance_health
from sentinelops.incidents.health_replay import replay_health_manifest
from sentinelops.telemetry.core import BucketMetrics, normalize_bucket, stable_id, timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import queries, SOURCE_PROFILE
from sentinelops.telemetry.store import ArtifactStore

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "configs/slo_health_policy.json").read_text())
END = datetime(2026, 10, 26, 0, 10, tzinfo=timezone.utc)


def observation(index, *, bad=False, count=120, coverage=1, delay=30):
    end = END + timedelta(minutes=index)
    return normalize_bucket(BucketMetrics("payment-service", end, count, 12 if bad else 0,
        ((0, 0), (100, count), (math.inf, count)), coverage,
        end + timedelta(seconds=delay), (f"fixture://bucket/{index}",)))


def advance(previous, index, obs=None, *, policy=POLICY, lag=60):
    end = END + timedelta(minutes=index)
    return advance_health(previous, obs, service="payment-service", environment="local",
        source_profile="prometheus-increase-60s/v1", bucket_end=end,
        evaluated_at=end + timedelta(seconds=lag), policy=policy)


def reidentify(observation):
    observation["record_id"] = stable_id({k: v for k, v in observation.items()
        if k not in ("record_id", "available_at", "ingested_at")})
    return observation


def test_sustained_incident_and_three_clean_bucket_recovery_keep_evidence_and_clocks():
    state = advance(None, 0, observation(0, bad=True))
    assert state["health"] == "degrading" and not state["incident_active"]
    before = deepcopy(state)
    opened = advance(state, 1, observation(1, bad=True))
    assert state == before
    assert opened["event"] == "incident_opened" and opened["incident_active"]
    assert opened["episode_start_at"] == timestamp(END - timedelta(minutes=1))
    assert opened["episode_confirmed_at"] == timestamp(END + timedelta(minutes=2))
    assert len(opened["evidence_refs"]) == 2
    first = advance(opened, 2, observation(2))
    second = advance(first, 3, observation(3))
    assert first["health"] == second["health"] == "recovering"
    recovered = advance(second, 4, observation(4))
    assert recovered["event"] == "recovery_confirmed" and not recovered["incident_active"]
    assert len(recovered["evidence_refs"]) == 3 and recovered["source"] == "slo"
    assert recovered["model_outputs_available"] is False
    fresh = advance(recovered, 5, observation(5, bad=True))
    assert fresh["episode_start_at"] != opened["episode_start_at"]


def test_duplicate_does_not_increment_and_revisions_or_reordering_need_explicit_replay():
    obs = observation(0, bad=True)
    state = advance(None, 0, obs)
    duplicate = advance(state, 0, obs)
    assert duplicate == state and duplicate["breach_streak"] == 1
    duplicate["evidence_refs"].clear()
    assert len(state["evidence_refs"]) == 1
    for index, item, lag in ((0, observation(0), 60), (0, obs, 61), (-1, observation(-1), 60)):
        with pytest.raises(ValueError, match="replay"):
            advance(state, index, item, lag=lag)


@pytest.mark.parametrize("problem", ["missing", "no_traffic", "coverage", "future", "ingestion", "stale"])
def test_unknown_never_clears_active_incident_or_counts_toward_recovery(problem):
    opened = advance(advance(None, 0, observation(0, bad=True)), 1, observation(1, bad=True))
    recovering = advance(opened, 2, observation(2))
    obs, lag = observation(3), 60
    if problem == "missing": obs = None
    elif problem == "no_traffic": obs = observation(3, count=0)
    elif problem == "coverage": obs = observation(3, coverage=.1)
    elif problem == "future": obs = observation(3, delay=61)
    elif problem == "ingestion": obs["ingested_at"] = timestamp(END + timedelta(minutes=4, seconds=1))
    else: lag = 61
    unknown = advance(recovering, 3, obs, lag=lag)
    assert unknown["availability"] == "UNKNOWN" and unknown["health"] is None
    assert unknown["incident_active"] and unknown["clear_streak"] == unknown["breach_streak"] == 0
    assert unknown["episode_confirmed_at"] == opened["episode_confirmed_at"]
    for index in (4, 5):
        unknown = advance(unknown, index, observation(index))
        assert unknown["health"] == "recovering"
    assert advance(unknown, 6, observation(6))["event"] == "recovery_confirmed"


def test_implicit_gap_resets_entry_and_recovery_and_a_rebreach_keeps_the_same_episode():
    first = advance(None, 0, observation(0, bad=True))
    assert advance(first, 2, observation(2, bad=True))["health"] == "degrading"
    opened = advance(first, 1, observation(1, bad=True))
    recovering = advance(opened, 2, observation(2))
    gap = advance(recovering, 4, observation(4))
    assert gap["health"] == "recovering" and gap["clear_streak"] == 1
    rebreach = advance(gap, 5, observation(5, bad=True))
    assert rebreach["health"] == "incident" and rebreach["event"] == "observation"
    assert rebreach["episode_start_at"] == opened["episode_start_at"]


def test_exact_thresholds_are_not_violations_and_forged_counts_or_record_ids_fail():
    obs = observation(0)
    obs["payload"].update(p95_latency_ms=750, error_rate=.05)
    obs["measurements"]["error_count"] = 6
    reidentify(obs)
    assert advance(None, 0, obs)["health"] == "healthy"
    obs["payload"]["p95_latency_ms"] = 750.0001
    with pytest.raises(ValueError, match="record identity"):
        advance(None, 0, obs)
    reidentify(obs)
    assert advance(None, 0, obs)["health"] == "degrading"
    obs["measurements"]["error_count"] = 8
    reidentify(obs)
    with pytest.raises(ValueError, match="ratio contradicts"):
        advance(None, 0, obs)


def test_checkpoint_and_input_cannot_cross_source_policy_or_temporal_boundaries():
    state = advance(None, 0, observation(0))
    for key in ("policy_sha256", "signal_id"):
        altered = deepcopy(state); altered[key] = "0" * 64
        with pytest.raises(ValueError): advance(altered, 1, observation(1))
    with pytest.raises(ValueError, match="policy differs"):
        advance(state, 1, observation(1), policy={**POLICY, "latency_ms": 751})
    obs = observation(1); obs["environment"] = "other"; reidentify(obs)
    with pytest.raises(ValueError, match="identity differs"): advance(state, 1, obs)
    with pytest.raises(ValueError, match="closed aligned"): advance(None, 0, observation(0), lag=-1)


@pytest.mark.parametrize("policy", [{**POLICY, "entry_buckets": True}, {**POLICY, "latency_ms": math.nan},
    {**POLICY, "minimum_requests": 0}, {**POLICY, "extra": 1}])
def test_bad_policy_fails_before_health_decisions(policy):
    with pytest.raises(ValueError): advance(None, 0, observation(0), policy=policy)


def capture(index, *, broken=False):
    end = END + timedelta(minutes=index)
    vectors = {"requests": [({}, 120)], "errors": [({}, 12)],
        "buckets": [({"le": "0"}, 0), ({"le": "100"}, 120), ({"le": "+Inf"}, 120)],
        "scrapes": [({}, 12)], "series_scrapes": [({}, 12)], "up": [({}, 1)]}
    raw = {"capture_version": "1.0", "source_profile": SOURCE_PROFILE, "service": "payment-service",
        "bucket_end": timestamp(end), "collected_at": timestamp(end + timedelta(seconds=30)),
        "queries": {key: {"expression": expression, "response": {"status": "success", "data": {
            "resultType": "vector", "result": [{"metric": labels, "value": [end.timestamp(), str(value)]}
                for labels, value in vectors[key]]}}} for key, expression in queries("payment-service").items()}}
    if broken: raw["queries"].pop("buckets")
    return raw


def test_replay_verifies_raw_parity_keeps_missing_and_quarantined_extents_and_is_immutable(tmp_path):
    store = ArtifactStore(tmp_path / "source")
    manifest = process_captures([capture(0), capture(1), capture(3, broken=True)], store)
    paths_before = sorted(path.relative_to(store.root) for path in store.root.rglob("*.json"))
    first = replay_health_manifest(store, manifest["manifest_ref"], POLICY, tmp_path / "one")
    second = replay_health_manifest(store, manifest["manifest_ref"], POLICY, tmp_path / "two")
    assert first == second and first["signal_count"] == 12
    assert first["source_quarantine_count"] == 1
    assert first["event_counts"]["payment-service/incident_opened"] == 1
    assert first["availability_counts"]["payment-service/UNKNOWN"] == 2
    assert all(first["final_states"][service]["health"] is None for service in first["final_states"])
    assert first["final_states"]["payment-service"]["incident_active"]
    assert paths_before == sorted(path.relative_to(store.root) for path in store.root.rglob("*.json"))
    dest = ArtifactStore(tmp_path / "one")
    for reference in first["signal_refs"]: validate_payload("health-signal.schema.json", dest.read(reference))
    assert dest.read(first["report_ref"])["raw_normalization_parity"]
    with pytest.raises(FileExistsError): replay_health_manifest(store, manifest["manifest_ref"], POLICY, tmp_path / "one")


def test_whole_source_outage_is_unknown_instead_of_an_empty_healthy_report(tmp_path):
    store = ArtifactStore(tmp_path / "source")
    manifest = process_captures([capture(0, broken=True), capture(1, broken=True)], store)
    report = replay_health_manifest(store, manifest["manifest_ref"], POLICY, tmp_path / "out")
    assert report["signal_count"] == 6 and report["source_observation_count"] == 0
    assert sum(report["availability_counts"].values()) == sum(v for k,v in report["availability_counts"].items() if k.endswith('/UNKNOWN'))


def test_rehashed_manifest_cannot_forge_source_replay_and_truth_metadata_is_rejected(tmp_path):
    store = ArtifactStore(tmp_path / "source")
    manifest = process_captures([capture(0)], store)
    forged = store.read(manifest["manifest_ref"]); forged["observation_refs"] = []
    reference = store.write("manifests", forged)
    with pytest.raises(ValueError, match="does not reproduce"):
        replay_health_manifest(store, reference, POLICY, tmp_path / "forged")
    assert not (tmp_path / "forged").exists()
    raw = capture(0); raw["scenario_id"] = "S3"
    injected = process_captures([raw], store)
    with pytest.raises(ValueError, match="control metadata"):
        replay_health_manifest(store, injected["manifest_ref"], POLICY, tmp_path / "truth")
