"""Verify one accepted continuous-health batch against its raw source history.

This is deterministic lineage/integrity verification of a selected ledger, not
authentication of a remote Prometheus server or proof of historical causality.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import re
from tempfile import TemporaryDirectory

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.incidents.health import advance_health, validate_policy
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import SOURCE_PROFILE
from sentinelops.telemetry.store import ArtifactStore

IDENTITY_KEYS = {"worker_version", "source_profile", "environment", "policy_sha256",
                 "prometheus_configuration_hash", "endpoint_hash", "artifact_root_hash",
                 "source_watermark_seconds", "query_timeout_seconds"}
BATCH_KEYS = {"worker_version", "stream_id", "identity", "bucket_end", "evaluated_at",
              "previous_batch_ref", "current_raw_refs", "normalization_manifest_ref", "signal_refs",
              "source_status", "model_outputs_available", "actions_allowed"}
RAW_KEYS = {"capture_version", "source_profile", "service", "bucket_end", "collected_at", "queries"}


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def at(value):
    return utc_time(value) if isinstance(value, str) else utc_time(timestamp(value))


def validate_stream(stream: dict, policy: dict) -> dict:
    validate_policy(policy)
    identity = decoded(stream["identity"])
    if (set(identity) != IDENTITY_KEYS or identity["worker_version"] != "continuous-health/v1"
        or identity["source_profile"] != SOURCE_PROFILE or not isinstance(identity["environment"], str)
        or not identity["environment"] or identity["policy_sha256"] != stable_id(policy)
        or type(identity["source_watermark_seconds"]) is not int or identity["source_watermark_seconds"] != 30
        or type(identity["query_timeout_seconds"]) not in (int, float) or identity["query_timeout_seconds"] != 1.
        or any(not isinstance(identity[key], str) or not re.fullmatch(r"[0-9a-f]{64}", identity[key])
               for key in ("prometheus_configuration_hash", "endpoint_hash", "artifact_root_hash"))
        or stable_id(identity) != stream["stream_id"]):
        raise ValueError("consumer source is not the selected continuous health profile/policy")
    initial = at(stream["initial_bucket_end"])
    if initial.timestamp() % 60:
        raise ValueError("consumer source initial bucket is not UTC aligned")
    return identity


def read_header(store: ArtifactStore, row: dict, identity: dict) -> dict:
    batch = store.read(row["manifest_ref"])
    end, evaluated = at(row["bucket_end"]), at(row["evaluated_at"])
    if (set(batch) != BATCH_KEYS or batch["worker_version"] != "continuous-health/v1"
        or batch["identity"] != identity or batch["stream_id"] != stable_id(identity)
        or row["stream_id"] != batch["stream_id"] or end.timestamp() % 60
        or batch["bucket_end"] != timestamp(end) or batch["evaluated_at"] != timestamp(evaluated)
        or evaluated < end or at(row["recorded_at"]) < evaluated
        or batch["model_outputs_available"] is not False or batch["actions_allowed"] is not False
        or batch["source_status"] not in ("queried", "missed-decision-deadline")):
        raise ValueError("accepted health batch differs from its immutable ledger/source identity")
    refs = batch["current_raw_refs"]
    if (not isinstance(refs, list) or refs != sorted(set(refs))
        or len(refs) != (3 if batch["source_status"] == "queried" else 0)):
        raise ValueError("accepted health batch has invalid current source coverage")
    return batch


def verify_health_batch(store: ArtifactStore, stream: dict, row: dict, signal_rows: list[dict],
                        history_rows: list[dict], previous: dict | None, *, policy: dict) -> dict:
    """Inputs must come from one consistent selected PostgreSQL read snapshot."""
    identity = validate_stream(stream, policy)
    batch = read_header(store, row, identity)
    end, initial, evaluated = at(row["bucket_end"]), at(stream["initial_bucket_end"]), at(row["evaluated_at"])
    if (stream["last_bucket_end"] is None or end > at(stream["last_bucket_end"])
        or end == at(stream["last_bucket_end"]) and row["manifest_ref"] != stream["last_batch_ref"]):
        raise ValueError("source batch is not included in the accepted stream checkpoint")
    previous = previous or {}
    if len(signal_rows) != len(BUSINESS_ROUTES):
        raise ValueError("accepted source does not have exactly three service decisions")
    expected_ends = []
    cursor = max(initial, end - timedelta(minutes=4))
    while cursor < end:
        expected_ends.append(cursor)
        cursor += timedelta(seconds=60)
    if [at(item["bucket_end"]) for item in history_rows] != expected_ends:
        raise ValueError("consumer source history skips, duplicates or mixes accepted buckets")
    history = [read_header(store, item, identity) for item in history_rows]
    prior_ref = history_rows[-1]["manifest_ref"] if history else None
    if end < initial or batch["previous_batch_ref"] != prior_ref:
        raise ValueError("consumer source batch predecessor differs from the accepted ledger")
    if end == initial:
        if previous:
            raise ValueError("first accepted source bucket cannot inherit another health scope")
    elif (set(previous) != set(BUSINESS_ROUTES)
          or any(s["bucket_end"] != timestamp(end - timedelta(seconds=60)) for s in previous.values())
          or sorted("artifact://signals/" + stable_id(s) + ".json" for s in previous.values()) != history[-1]["signal_refs"]):
        raise ValueError("consumer source prior health states do not match accepted history")
    normalized = store.read(batch["normalization_manifest_ref"])
    expected_raw = sorted({ref for item in [*history, batch] for ref in item["current_raw_refs"]})
    if normalized["raw_refs"] != expected_raw:
        raise ValueError("normalization contains raw inputs outside accepted source history")
    captures, keys = [], set()
    for ref in expected_raw:
        raw = store.read(ref)
        if (set(raw) != RAW_KEYS or raw["capture_version"] != "1.0"
            or raw["source_profile"] != identity["source_profile"] or raw["service"] not in BUSINESS_ROUTES
            or not max(initial, end - timedelta(minutes=4)) <= utc_time(raw["bucket_end"]) <= end
            or (raw["service"], raw["bucket_end"]) in keys):
            raise ValueError("consumer source raw capture is foreign, duplicated or control-enriched")
        keys.add((raw["service"], raw["bucket_end"]))
        captures.append(raw)
    for item in [*history, batch]:
        current = [store.read(ref) for ref in item["current_raw_refs"]]
        if current and ({c["service"] for c in current} != set(BUSINESS_ROUTES)
                        or any(c["bucket_end"] != item["bucket_end"] for c in current)):
            raise ValueError("consumer source current captures do not resolve their accepted bucket")
    with TemporaryDirectory(prefix="sentinelops-health-lineage-") as temporary:
        recomputed = process_captures(captures, ArtifactStore(temporary), environment=identity["environment"])
    if recomputed["manifest_ref"] != batch["normalization_manifest_ref"]:
        raise ValueError("accepted health normalization does not reproduce from raw captures")
    observations = {}
    for ref in normalized["observation_refs"]:
        observation = store.read(ref)
        if observation["bucket_end"] == batch["bucket_end"]:
            if observation["service"] in observations:
                raise ValueError("consumer source has conflicting current observations")
            observations[observation["service"]] = observation
    for key in ("feature_refs", "quarantine_refs"):
        for ref in normalized[key]: store.read(ref)
    signals, objects = {}, []
    for record in signal_rows:
        signal = decoded(record["payload"])
        validate_payload("health-signal.schema.json", signal)
        service = signal["service"]
        if (service in signals or record["stream_id"] != stream["stream_id"] or at(record["bucket_end"]) != end
            or record["service"] != service or record["signal_id"] != signal["signal_id"]
            or store.read(record["signal_ref"]) != signal):
            raise ValueError("accepted signal row differs from its source/artifact identity")
        computed = advance_health(previous.get(service), observations.get(service), service=service,
            environment=identity["environment"], source_profile=identity["source_profile"],
            bucket_end=end, evaluated_at=evaluated, policy=policy)
        if signal != computed:
            raise ValueError("accepted health signal does not reproduce at its actual decision clock")
        signals[service] = signal
        objects.append((record["signal_ref"], signal))
    if (set(signals) != set(BUSINESS_ROUTES) or len(signal_rows) != 3
        or batch["signal_refs"] != sorted(ref for ref, _ in objects)):
        raise ValueError("accepted source omits or duplicates one of its three decisions")
    return {"batch": batch, "signal_objects": objects, "service_states": signals,
            "normalization": normalized, "raw_health_lineage_verified": True}


class HealthLedgerReader:
    """Read a selected live ledger view, then verify files outside its SQL tx."""

    def __init__(self, pool, store: ArtifactStore, stream_id: str, policy: dict):
        validate_policy(policy)
        if not isinstance(stream_id, str) or not re.fullmatch(r"[0-9a-f]{64}", stream_id):
            raise ValueError("continuous incident input needs an explicit health stream identity")
        self.pool, self.store, self.stream_id = pool, store, stream_id
        self.policy = json.loads(json.dumps(policy, allow_nan=False))

    async def next(self, checkpoint: dict | None = None) -> dict | None:
        cursor = at(checkpoint["last_bucket_end"]) if checkpoint is not None else None
        if checkpoint is not None and checkpoint["source"]["health_stream_id"] != self.stream_id:
            raise ValueError("consumer checkpoint belongs to a different health stream")
        async with self.pool.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
            stream = await conn.fetchrow("SELECT * FROM sentinel_control.health_streams WHERE stream_id=$1", self.stream_id)
            if stream is None:
                raise ValueError("selected continuous health stream does not exist")
            identity = validate_stream(stream, self.policy)
            if identity["artifact_root_hash"] != stable_id({"root": str(self.store.root)}):
                raise ValueError("live consumer store root differs from the selected source identity")
            last = stream["last_bucket_end"]
            if last is None:
                if cursor is not None:
                    raise ValueError("source checkpoint disappeared behind a committed consumer")
                return None
            if cursor is not None and cursor > last:
                raise ValueError("source checkpoint moved backwards behind the consumer")
            row = await conn.fetchrow("""SELECT * FROM sentinel_control.health_batches
                WHERE stream_id=$1 AND ($2::timestamptz IS NULL OR bucket_end>$2) AND bucket_end<=$3
                ORDER BY bucket_end LIMIT 1""", self.stream_id, cursor, last)
            if row is None:
                if cursor != last:
                    raise ValueError("accepted health checkpoint has no corresponding ledger history")
                return None
            lower = max(stream["initial_bucket_end"], row["bucket_end"] - timedelta(minutes=4))
            history = await conn.fetch("""SELECT * FROM sentinel_control.health_batches
                WHERE stream_id=$1 AND bucket_end>=$2 AND bucket_end<$3 ORDER BY bucket_end LIMIT 5""",
                self.stream_id, lower, row["bucket_end"])
            records = await conn.fetch("""SELECT * FROM sentinel_control.health_signals
                WHERE stream_id=$1 AND bucket_end=$2 ORDER BY service LIMIT 4""", self.stream_id, row["bucket_end"])
            observed = await conn.fetchval("SELECT clock_timestamp()")
        if at(row["recorded_at"]) > observed:
            raise ValueError("accepted health receipt clock is ahead of the source database")
        if checkpoint is not None and (not history or history[-1]["manifest_ref"] != checkpoint["last_batch_ref"]):
            raise ValueError("consumer pointer differs from its selected source predecessor")
        verified = await asyncio.to_thread(verify_health_batch, self.store, dict(stream), dict(row),
            [dict(r) for r in records], [dict(r) for r in history],
            checkpoint["service_states"] if checkpoint is not None else None, policy=self.policy)
        return {**verified, "source_stream": dict(stream), "accepted_row": dict(row),
                "source_read_at": timestamp(observed)}
