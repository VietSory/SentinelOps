from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from sentinelops.incidents.health import advance_health
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import SOURCE_PROFILE, queries, decode_capture
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health import HealthWorker
from sentinelops.workers.health_source import verify_health_batch

POLICY = json.loads(Path("configs/slo_health_policy.json").read_text())
START = datetime(2026, 10, 29, tzinfo=timezone.utc)


def capture(service, end, *, errors=12):
    values = {"requests": [({}, 120)], "errors": [({}, errors)],
              "buckets": [({"le": "0"}, 0), ({"le": "100"}, 120), ({"le": "+Inf"}, 120)],
              "scrapes": [({}, 12)], "series_scrapes": [({}, 12)], "up": [({}, 1)]}
    return {"capture_version": "1.0", "source_profile": SOURCE_PROFILE, "service": service,
            "bucket_end": timestamp(end), "collected_at": timestamp(end + timedelta(seconds=30)),
            "queries": {key: {"expression": expression, "response": {"status": "success", "data": {
                "resultType": "vector", "result": [{"metric": labels, "value": [end.timestamp(), str(value)]}
                    for labels, value in values[key]]}}} for key, expression in queries(service).items()}}


def ledger(tmp_path, *, broken=False):
    """Real codec/store software fixture, independent of PostgreSQL integration."""
    store = ArtifactStore(tmp_path)
    worker = HealthWorker(None, store, POLICY, endpoint="http://localhost:9090",
                          environment="fixture", configuration_hash="a" * 64)
    batches, records, previous, raws, previous_ref = [], [], {}, [], None
    for index in range(2):
        end, evaluated = START + timedelta(minutes=index), START + timedelta(minutes=index, seconds=37 + index)
        current = [capture(service, end) for service in sorted(BUSINESS_ROUTES)]
        if broken: current[0]["queries"].pop("buckets")
        refs = sorted(store.write("raw", raw) for raw in current)
        raws.extend(current)
        normalized = process_captures(raws, store, environment="fixture")
        observations = {o["service"]: o for ref in normalized["observation_refs"]
                        if (o := store.read(ref))["bucket_end"] == timestamp(end)}
        signals, signal_refs = {}, []
        for service in sorted(BUSINESS_ROUTES):
            signal = advance_health(previous.get(service), observations.get(service), service=service,
                environment="fixture", source_profile=SOURCE_PROFILE, bucket_end=end, evaluated_at=evaluated, policy=POLICY)
            signals[service] = signal
            ref = store.write("signals", signal)
            signal_refs.append(ref)
            records.append({"stream_id": worker.stream_id, "bucket_end": end, "service": service,
                "signal_id": signal["signal_id"], "signal_ref": ref, "payload": json.dumps(signal)})
        batch = {"worker_version": "continuous-health/v1", "stream_id": worker.stream_id, "identity": worker.identity,
            "bucket_end": timestamp(end), "evaluated_at": timestamp(evaluated), "previous_batch_ref": previous_ref,
            "current_raw_refs": refs, "normalization_manifest_ref": normalized["manifest_ref"],
            "signal_refs": sorted(signal_refs), "source_status": "queried", "model_outputs_available": False, "actions_allowed": False}
        previous_ref = store.write("manifests", batch)
        batches.append({"stream_id": worker.stream_id, "bucket_end": end, "manifest_ref": previous_ref,
                        "evaluated_at": evaluated, "recorded_at": evaluated + timedelta(seconds=1)})
        previous = signals
    stream = {"stream_id": worker.stream_id, "identity": json.dumps(worker.identity), "initial_bucket_end": START,
              "last_bucket_end": batches[-1]["bucket_end"], "last_batch_ref": batches[-1]["manifest_ref"]}
    return store, stream, batches, records


def verify_at(source, index, previous=None):
    store, stream, batches, records = source
    row = batches[index]
    rows = [r for r in records if r["bucket_end"] == row["bucket_end"]]
    return verify_health_batch(store, stream, row, rows, batches[max(0, index - 4):index], previous, policy=POLICY)


@pytest.mark.parametrize("broken", [False, True])
def test_actual_decision_clock_replays_raw_and_retains_quarantined_unknown(tmp_path, broken):
    source = ledger(tmp_path, broken=broken)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*.json')}
    first = verify_at(source, 0)
    last = verify_at(source, 1, first["service_states"])
    assert last["raw_health_lineage_verified"] is True
    assert last["batch"]["evaluated_at"].endswith("01:38Z")
    assert len(last["signal_objects"]) == 3
    if broken:
        assert last["normalization"]["quarantine_refs"]
        assert last["service_states"]["checkout-service"]["availability"] == "UNKNOWN"
        assert last["service_states"]["checkout-service"]["health"] is None
    else:
        assert all(s["event"] == "incident_opened" for s in last["service_states"].values())
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*.json')} == before


@pytest.mark.parametrize("problem", ["pointer", "incomplete", "history", "prior-state", "signal-bytes", "clock", "missed", "control-raw", "unaccepted-history", "forged-signal"])
def test_hash_valid_or_corrupt_foreign_evidence_cannot_become_accepted_health(tmp_path, problem):
    source = ledger(tmp_path)
    store, stream, batches, records = source
    previous = verify_at(source, 0)["service_states"]
    row = batches[1]
    signal_rows = [r for r in records if r["bucket_end"] == row["bucket_end"]]
    history = batches[:1]
    batch = store.read(row["manifest_ref"])
    if problem == "pointer": stream["last_bucket_end"] = batches[0]["bucket_end"]
    elif problem == "incomplete": signal_rows.pop()
    elif problem == "history": history = []
    elif problem == "prior-state": previous = {}
    elif problem == "signal-bytes":
        (store.root / signal_rows[0]["signal_ref"].removeprefix("artifact://")).write_text('{}')
    elif problem == "clock": row["recorded_at"] = row["evaluated_at"] - timedelta(seconds=1)
    elif problem == "missed": batch["source_status"] = "missed-decision-deadline"
    elif problem in ("control-raw", "unaccepted-history"):
        normalized = store.read(batch["normalization_manifest_ref"])
        raws = [store.read(ref) for ref in normalized["raw_refs"]]
        if problem == "control-raw":
            target = next(r for r in raws if r["bucket_end"] == batch["bucket_end"])
            old = store.write("raw", target)
            target["root_cause_ground_truth"] = "payment-service"
            new = store.write("raw", target)
            batch["current_raw_refs"] = sorted(new if ref == old else ref for ref in batch["current_raw_refs"])
        else:
            target = next(r for r in raws if r["bucket_end"] != batch["bucket_end"])
            target["collected_at"] = timestamp(START + timedelta(seconds=31))
        norm = process_captures(raws, store, environment="fixture")
        batch["normalization_manifest_ref"] = norm["manifest_ref"]
    elif problem == "forged-signal":
        record = signal_rows[0]
        observation = decode_capture(capture(record["service"], row["bucket_end"], errors=0), environment="fixture")
        signal = advance_health(previous[record["service"]], observation, service=record["service"], environment="fixture",
            source_profile=SOURCE_PROFILE, bucket_end=row["bucket_end"], evaluated_at=row["evaluated_at"], policy=POLICY)
        old = record["signal_ref"]
        record.update(payload=json.dumps(signal), signal_id=signal["signal_id"], signal_ref=store.write("signals", signal))
        batch["signal_refs"] = sorted(record["signal_ref"] if ref == old else ref for ref in batch["signal_refs"])
    if problem in ("missed", "control-raw", "unaccepted-history", "forged-signal"):
        row["manifest_ref"] = store.write("manifests", batch)
        stream["last_batch_ref"] = row["manifest_ref"]
    with pytest.raises(ValueError):
        verify_health_batch(store, stream, row, signal_rows, history, previous, policy=POLICY)
