"""Actual-clock local SLO producer with atomic per-bucket PostgreSQL checkpoints."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import re
import time
from urllib.parse import urlparse

import httpx
import asyncpg

from sentinelops.contracts import validate_payload
from sentinelops.incidents.health import advance_health, validate_policy
from sentinelops.incidents.repository import IncidentRepository
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import PrometheusSource, SOURCE_PROFILE
from sentinelops.telemetry.store import ArtifactStore


def local_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    try:
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("invalid port")
    except ValueError:
        raise ValueError("local health worker requires a valid loopback port") from None
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost")
        or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
        or parsed.path not in ("", "/")):
        raise ValueError("local health worker requires a credential-free loopback Prometheus endpoint")
    return endpoint.rstrip("/")


def latest_bucket(now: datetime) -> datetime:
    timestamp(now)
    return datetime.fromtimestamp(int((now - timedelta(seconds=30)).timestamp()) // 60 * 60, timezone.utc)


def native_capture(endpoint: str, service: str, end: datetime) -> dict:
    source = PrometheusSource(endpoint)
    source.client.timeout = httpx.Timeout(1.)
    try:
        return source.capture(service, end)
    finally:
        source.close()


class HealthWorker:
    def __init__(self, pool, store: ArtifactStore, policy: dict, *, endpoint: str, environment: str,
                 configuration_hash: str):
        validate_policy(policy)
        if not environment or not re.fullmatch(r"[0-9a-f]{64}", configuration_hash):
            raise ValueError("health worker source identity is incomplete")
        self.pool, self.store = pool, store
        self.policy = json.loads(json.dumps(policy, allow_nan=False))
        self.endpoint, self.environment = local_endpoint(endpoint), environment
        self.identity = {"worker_version": "continuous-health/v1", "source_profile": SOURCE_PROFILE,
                         "environment": environment, "policy_sha256": stable_id(policy),
                         "prometheus_configuration_hash": configuration_hash,
                         "endpoint_hash": stable_id({"endpoint": self.endpoint}),
                         "artifact_root_hash": stable_id({"root": str(store.root)}),
                         "source_watermark_seconds": 30, "query_timeout_seconds": 1.}
        self.stream_id = stable_id(self.identity)
        lock = int(self.stream_id[:16], 16)
        self.lock_id = lock if lock < 2 ** 63 else lock - 2 ** 64

    async def check_ready(self):
        await IncidentRepository(self.pool).check_ready()
        async with self.pool.acquire() as conn:
            versions = await conn.fetchval("SELECT array_agg(version ORDER BY version) FROM sentinel_control.schema_migrations")
            if versions != [1, 2]:
                raise ValueError("health worker requires explicit additive control migration 2")
            unsafe = await conn.fetchval("""SELECT
                has_table_privilege('sentinel_control.health_batches','UPDATE,DELETE,TRUNCATE')
                OR has_table_privilege('sentinel_control.health_signals','UPDATE,DELETE,TRUNCATE')
                OR has_column_privilege('sentinel_control.health_streams','identity','UPDATE')""")
            if unsafe:
                raise ValueError("health worker runtime can mutate immutable history/identity")

    async def status(self) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""SELECT s.last_bucket_end,s.last_batch_ref,
                (SELECT count(*) FROM sentinel_control.health_batches WHERE stream_id=$1) AS batches,
                (SELECT count(*) FROM sentinel_control.health_signals WHERE stream_id=$1) AS signals
                FROM (SELECT $1::text AS stream_id) requested
                LEFT JOIN sentinel_control.health_streams s USING (stream_id)""", self.stream_id)
        return {"stream_id": self.stream_id, "last_bucket_end": timestamp(row["last_bucket_end"]) if row["last_bucket_end"] else None,
                "last_batch_ref": row["last_batch_ref"], "batches": row["batches"], "signals": row["signals"]}

    async def tick(self) -> dict:
        started = time.monotonic()
        async with self.pool.acquire() as conn:
            if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", self.lock_id):
                return {"status": "BUSY", "stream_id": self.stream_id}
            try:
                result = await self._locked_tick(conn, started)
            except asyncpg.InterfaceError:
                # A disconnect can arrive while capture runs outside the SQL transaction.
                # Reclassify only a confirmed closed connection; other API misuse is fatal.
                if conn.is_closed():
                    raise ConnectionError("health database connection closed during bucket processing") from None
                raise
            finally:
                if not conn.is_closed():
                    try:
                        await conn.fetchval("SELECT pg_advisory_unlock($1)", self.lock_id)
                    except (asyncpg.InterfaceError, asyncpg.PostgresConnectionError):
                        if not conn.is_closed():
                            raise
        if result["status"] == "COMMITTED":
            result["processing_ms"] = round((time.monotonic() - started) * 1000, 3)
        return result

    async def _locked_tick(self, conn, started: float) -> dict:
        current = datetime.now(timezone.utc)
        await conn.execute("""INSERT INTO sentinel_control.health_streams(stream_id,identity,initial_bucket_end)
            VALUES ($1,$2::jsonb,$3) ON CONFLICT DO NOTHING""", self.stream_id,
            json.dumps(self.identity), latest_bucket(current))
        stream = await conn.fetchrow("SELECT * FROM sentinel_control.health_streams WHERE stream_id=$1", self.stream_id)
        if json.loads(stream["identity"]) != self.identity:
            raise ValueError("durable health stream identity differs from runtime")
        previous_end = stream["last_bucket_end"]
        end = previous_end + timedelta(seconds=60) if previous_end else stream["initial_bucket_end"]
        if current < end + timedelta(seconds=30):
            return {"status": "WAIT", "stream_id": self.stream_id, "next_bucket_end": timestamp(end)}
        previous = {}
        if previous_end:
            records = await conn.fetch("SELECT * FROM sentinel_control.health_signals WHERE stream_id=$1 AND bucket_end=$2",
                                       self.stream_id, previous_end)
            for record in records:
                payload = json.loads(record["payload"])
                validate_payload("health-signal.schema.json", payload)
                if (record["signal_id"] != payload["signal_id"] or self.store.read(record["signal_ref"]) != payload
                    or payload["bucket_end"] != timestamp(previous_end) or payload["service"] != record["service"]):
                    raise ValueError("durable health signal/artifact integrity differs")
                previous[record["service"]] = payload
            if set(previous) != set(BUSINESS_ROUTES):
                raise ValueError("durable health checkpoint omits a business service")
            prior_manifest = self.store.read(stream["last_batch_ref"])
            if (prior_manifest["stream_id"] != self.stream_id or prior_manifest["bucket_end"] != timestamp(previous_end)
                or prior_manifest["signal_refs"] != sorted(record["signal_ref"] for record in records)):
                raise ValueError("durable health batch checkpoint differs")
        history = await conn.fetch("""SELECT manifest_ref,bucket_end FROM sentinel_control.health_batches
            WHERE stream_id=$1 AND bucket_end>=$2 AND bucket_end<$3 ORDER BY bucket_end""",
            self.stream_id, end - timedelta(minutes=4), end)
        captures = []
        for record in history:
            manifest = self.store.read(record["manifest_ref"])
            if (manifest["stream_id"] != self.stream_id or manifest["identity"] != self.identity
                or manifest["bucket_end"] != timestamp(record["bucket_end"])):
                raise ValueError("health feature history belongs to another source/bucket")
            captures.extend(self.store.read(ref) for ref in manifest["current_raw_refs"])
        missed = (current - end).total_seconds() > self.policy["maximum_lag_seconds"]
        history_done = time.monotonic()
        current_captures = [] if missed else await asyncio.gather(*(
            asyncio.to_thread(native_capture, self.endpoint, service, end) for service in sorted(BUSINESS_ROUTES)))
        if not missed and (len(current_captures) != len(BUSINESS_ROUTES)
                           or {item["service"] for item in current_captures} != set(BUSINESS_ROUTES)
                           or any(item["bucket_end"] != timestamp(end) for item in current_captures)):
            raise ValueError("current source capture omits/duplicates/mixes business buckets")
        allowed = {"capture_version", "source_profile", "service", "bucket_end", "collected_at", "queries"}
        for capture in captures + current_captures:
            if (set(capture) != allowed or capture["capture_version"] != "1.0"
                or capture["source_profile"] != SOURCE_PROFILE or capture["service"] not in BUSINESS_ROUTES):
                raise ValueError("health worker rejects mixed source/control metadata")
        capture_done = time.monotonic()
        current_refs = sorted(self.store.write("raw", capture) for capture in current_captures)
        normalized = await asyncio.to_thread(process_captures, captures + current_captures, self.store, environment=self.environment)
        observations = {}
        for ref in normalized["observation_refs"]:
            observation = self.store.read(ref)
            if observation["bucket_end"] == timestamp(end):
                if observation["service"] in observations:
                    raise ValueError("health worker has conflicting current observations")
                observations[observation["service"]] = observation
        normalization_done = time.monotonic()
        evaluated = datetime.now(timezone.utc)
        signals, signal_refs = [], []
        for service in sorted(BUSINESS_ROUTES):
            signal = advance_health(previous.get(service), observations.get(service), service=service,
                environment=self.environment, source_profile=SOURCE_PROFILE, bucket_end=end,
                evaluated_at=evaluated, policy=self.policy)
            signals.append(signal)
            signal_refs.append(self.store.write("signals", signal))
        batch = {"worker_version": "continuous-health/v1", "stream_id": self.stream_id, "identity": self.identity,
                 "bucket_end": timestamp(end), "evaluated_at": timestamp(evaluated),
                 "previous_batch_ref": stream["last_batch_ref"], "current_raw_refs": current_refs,
                 "normalization_manifest_ref": normalized["manifest_ref"], "signal_refs": sorted(signal_refs),
                 "source_status": "missed-decision-deadline" if missed else "queried",
                 "model_outputs_available": False, "actions_allowed": False}
        ref = self.store.write("manifests", batch)
        async with conn.transaction():
            await conn.execute("""INSERT INTO sentinel_control.health_batches(stream_id,bucket_end,manifest_ref,evaluated_at)
                VALUES ($1,$2,$3,$4)""", self.stream_id, end, ref, evaluated)
            for signal, signal_ref in zip(signals, signal_refs):
                await conn.execute("""INSERT INTO sentinel_control.health_signals
                    (stream_id,bucket_end,service,signal_id,signal_ref,payload) VALUES ($1,$2,$3,$4,$5,$6::jsonb)""",
                    self.stream_id, end, signal["service"], signal["signal_id"], signal_ref, json.dumps(signal))
            updated = await conn.execute("""UPDATE sentinel_control.health_streams
                SET last_bucket_end=$2,last_batch_ref=$3,updated_at=clock_timestamp()
                WHERE stream_id=$1 AND last_bucket_end IS NOT DISTINCT FROM $4::timestamptz""",
                self.stream_id, end, ref, previous_end)
            if updated != "UPDATE 1":
                raise ValueError("health checkpoint compare-and-set failed")
        return {"status": "COMMITTED", "stream_id": self.stream_id, "bucket_end": timestamp(end),
                "manifest_ref": ref, "stages_ms": {
                    "checkpoint_and_history": round((history_done - started) * 1000, 3),
                    "capture": round((capture_done - history_done) * 1000, 3),
                    "normalization": round((normalization_done - capture_done) * 1000, 3),
                    "decision_and_commit": round((time.monotonic() - normalization_done) * 1000, 3)}}
