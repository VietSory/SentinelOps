"""Synthetic source tests; optional PostgreSQL tests are storage, not live detection."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import asyncpg
import pytest
import yaml

from sentinelops.contracts import utc_time
from sentinelops.incidents.health import advance_health
from sentinelops.incidents.rca import validate_result
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health import HealthWorker
from sentinelops.workers.health_source import verify_health_batch
from sentinelops.workers.incident_state import advance_groups
from sentinelops.workers.legacy_incident_projection import evaluate_cluster, incident_commands
from sentinelops.workers.legacy_incident_replay import replay_incident_history
from sentinelops.telemetry.core import stable_id
from test_health_source import capture

HEALTH = json.loads(Path("configs/slo_health_policy.json").read_text())
RCA = json.loads(Path("configs/rca_policy.json").read_text())
TOPOLOGY = yaml.safe_load(Path("configs/topology.yaml").read_text())
PAY, INV, CHECK = "payment-service", "inventory-service", "checkout-service"


def source_fixture(root, failures):
    store = ArtifactStore(root)
    worker = HealthWorker(None, store, HEALTH, endpoint="http://localhost:9090", environment="fixture", configuration_hash="a" * 64)
    start = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=1)
    previous, prior_ref, raws, batches, records = {}, None, [], [], []
    for i, bad in enumerate(failures):
        end = start + timedelta(minutes=i)
        evaluated = end + timedelta(seconds=35)
        current = [capture(s, end, errors=12 if bad is not None and s in bad else 0) for s in sorted(BUSINESS_ROUTES)]
        if bad is None:
            for raw in current:
                raw["queries"].pop("buckets")
        refs = sorted(store.write("raw", raw) for raw in current)
        raws.extend(current)
        normalized = process_captures(raws[-15:], store, environment="fixture")
        observations = {o["service"]: o for ref in normalized["observation_refs"] if (o := store.read(ref))["bucket_end"] == timestamp(end)}
        current_states, signal_refs = {}, []
        for service in sorted(BUSINESS_ROUTES):
            signal = advance_health(previous.get(service), observations.get(service), service=service, environment="fixture",
                source_profile=worker.identity["source_profile"], bucket_end=end, evaluated_at=evaluated, policy=HEALTH)
            current_states[service] = signal
            ref = store.write("signals", signal)
            signal_refs.append(ref)
            records.append({"stream_id": worker.stream_id, "bucket_end": end, "service": service,
                "signal_id": signal["signal_id"], "signal_ref": ref, "payload": json.dumps(signal)})
        batch = {"worker_version": "continuous-health/v1", "stream_id": worker.stream_id, "identity": worker.identity,
            "bucket_end": timestamp(end), "evaluated_at": timestamp(evaluated), "previous_batch_ref": prior_ref,
            "current_raw_refs": refs, "normalization_manifest_ref": normalized["manifest_ref"], "signal_refs": sorted(signal_refs),
            "source_status": "queried", "model_outputs_available": False, "actions_allowed": False}
        prior_ref = store.write("manifests", batch)
        batches.append({"stream_id": worker.stream_id, "bucket_end": end, "manifest_ref": prior_ref,
                        "evaluated_at": evaluated, "recorded_at": evaluated + timedelta(seconds=1)})
        previous = current_states
    stream = {"stream_id": worker.stream_id, "identity": json.dumps(worker.identity), "initial_bucket_end": start,
              "last_bucket_end": end, "last_batch_ref": prior_ref}
    return store, stream, batches, records


@pytest.mark.parametrize("bad,expected", [(set(), []), ({PAY}, [[PAY]]), ({PAY, CHECK}, [[CHECK, PAY]]),
    ({PAY, INV}, [[INV], [PAY]]), ({PAY, INV, CHECK}, [[CHECK], [INV], [PAY]])])
def test_verified_source_to_existing_rca_and_ier(tmp_path, bad, expected):
    store, stream, batches, records = source_fixture(tmp_path, [bad] * 5)
    state, closed = None, []
    for i, row in enumerate(batches):
        verified = verify_health_batch(store, stream, row, [r for r in records if r["bucket_end"] == row["bucket_end"]],
            batches[max(0, i - 4):i], state["service_states"] if state else None, policy=HEALTH)
        state, emitted = advance_groups(state, verified["batch"], verified["signal_objects"], TOPOLOGY, HEALTH)
        closed.extend(emitted)
    affected = sorted(g["affected_services"] for item in closed for g in item["correlation"]["groups"])
    assert affected == expected
    for item in closed:
        rows = evaluate_cluster(store, item, policy=RCA, as_of=datetime.now(timezone.utc), health_policy=HEALTH)
        for row in rows:
            validate_result(row["rca"])
            assert row["rca"]["abstained"] and not row["rca"]["actions_allowed"]
            assert all(c["components"]["anomaly_strength"]["value"] is None for c in row["rca"]["candidates"])
        commands = incident_commands("a" * 64, item, rows, timestamp(datetime.now(timezone.utc)))
        assert len(commands) == len(expected) * 3
        for command in commands:
            command.validate()
            assert command.payload["service_signals"] == []
            for fact in command.payload["evidence"]:
                store.read(fact["source_ref"])
        assert commands == incident_commands("a" * 64, item, rows, commands[0].payload["detected_at"])


def test_missing_after_opening_retains_active_episode_and_analytical_abstention(tmp_path):
    store, stream, batches, records = source_fixture(tmp_path, [{PAY}] * 2 + [None] * 3)
    state, results = None, []
    for row in batches:
        batch = store.read(row["manifest_ref"])
        signals = [(r["signal_ref"], json.loads(r["payload"])) for r in records if r["bucket_end"] == row["bucket_end"]]
        state, emitted = advance_groups(state, batch, signals, TOPOLOGY, HEALTH)
        results.extend(emitted)
    assert len(results) == 1
    assert state["service_states"][PAY]["availability"] == "UNKNOWN"
    assert state["service_states"][PAY]["incident_active"] is True


@pytest.mark.parametrize("include_slo_values", [False, True])
def test_offline_continuous_history_reproduces_and_rejects_rehashed_results(tmp_path, include_slo_values):
    store, stream, batches, records = source_fixture(tmp_path, [{PAY}] * 5)
    from types import SimpleNamespace
    identity = {"worker_version": "continuous-incidents/v1", "health_stream_id": stream["stream_id"],
        "topology_sha256": stable_id(TOPOLOGY), "health_policy_sha256": stable_id(HEALTH),
        "rca_policy_sha256": stable_id(RCA), "evidence_profile": "slo-only/v1"}
    worker = SimpleNamespace(identity=identity, consumer_id=stable_id(identity))
    state, prior, refs = None, None, []
    for row in batches:
        batch = store.read(row["manifest_ref"])
        signals = [(r["signal_ref"], json.loads(r["payload"])) for r in records if r["bucket_end"] == row["bucket_end"]]
        state, closed = advance_groups(state, batch, signals, TOPOLOGY, HEALTH)
        as_of = timestamp(datetime.now(timezone.utc))
        results, commands = [], []
        for item in closed:
            output = evaluate_cluster(store, item, policy=RCA, as_of=utc_time(as_of), health_policy=HEALTH, include_slo_values=include_slo_values)
            results.extend(output)
            commands.extend(incident_commands(worker.consumer_id, item, output, as_of))
        commit = {"consumer_id": worker.consumer_id, "previous_commit_ref": prior, "state": state,
                  "projection_version": "continuous-ier/v2" if include_slo_values else "continuous-ier/v1",
                  "closed_clusters": closed, "processed_at": as_of, "modalities": [None] * len(closed),
                  "results": results, "commands": [c.__dict__ for c in commands]}
        prior = store.write("manifests", commit); refs.append(prior)
    snapshot = {"stream": stream, "batches": batches, "signals": records, "consumer_identity": worker.identity, "consumer_commit_refs": refs}
    result = replay_incident_history(store, snapshot, TOPOLOGY, HEALTH, RCA)
    assert result["incidents"] == 1 and result["remaining_accepted_buckets"] == 0
    corrupt = store.read(refs[-1]); corrupt["commands"] = [{"fabricated": True}]
    changed = deepcopy(snapshot); changed["consumer_commit_refs"][-1] = store.write("manifests", corrupt)
    with pytest.raises(ValueError, match="projection"):
        replay_incident_history(store, changed, TOPOLOGY, HEALTH, RCA)
