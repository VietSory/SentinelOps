"""Continuous accepted-health consumer with atomic IER/checkpoint commits.

Source silence never finalizes a cluster. Each accepted bucket is verified from
raw captures before the existing event-time reducer sees it. Files may be
orphaned by a failed transaction; only the database ledger accepts a commit.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import time

import asyncpg

from sentinelops.contracts import utc_time
from sentinelops.incidents.correlation import topology_paths
from sentinelops.incidents.domain import IncidentWrite
from sentinelops.incidents.rca import validate_policy
from sentinelops.incidents.rca_replay import business_logs, derive_group
from sentinelops.incidents.repository import IncidentRepository
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health_source import HealthLedgerReader
from sentinelops.workers.incident_state import advance_groups


def evaluate_cluster(store, closed, *, policy, as_of, health_policy, modalities=None):
    """Reuse native evidence derivation; no experiment truth or invented scores."""
    correlation = closed["correlation"]
    correlation_ref = store.write("manifests", correlation)
    scope_ref = store.write("manifests", closed["scope"])
    if scope_ref != closed["scope_ref"]:
        raise ValueError("continuous cluster identity differs")
    if modalities is None:
        modalities = {"logs": store.write("raw", {"kind": "serving-logs", "records": []}),
                      "traces": [], "infrastructure": []}
    if set(modalities) != {"logs", "traces", "infrastructure"} or len(modalities["traces"]) > 200 or len(modalities["infrastructure"]) > 500:
        raise ValueError("continuous evidence exceeds the native bounded profile")
    signals = {item["signal"]["signal_id"]: (item["signal_ref"], item["signal"]) for item in closed["opening_objects"]}
    refs = {scope_ref, correlation_ref, modalities["logs"], *modalities["traces"], *modalities["infrastructure"]}
    for ref, signal in signals.values():
        if store.read(ref) != signal:
            raise ValueError("continuous opening differs from verified source object")
        refs.add(ref)
        refs.update(signal["evidence_refs"])
    for ref in refs:
        store.read(ref)
    logs = store.read(modalities["logs"])
    if logs.get("kind") not in ("logs", "serving-logs") or not isinstance(logs.get("records"), list) or len(logs["records"]) > 250000:
        raise ValueError("unsupported continuous completion logs")
    scope = {"correlation": correlation, "correlation_ref": correlation_ref,
             "health": {"policy": health_policy}, "source": {"environment": correlation["source_identity"][0]},
             "signals": signals, "refs": modalities, "allowed_refs": sorted(refs), "logs": logs,
             "traces": [(ref, store.read(ref)) for ref in modalities["traces"]],
             "infrastructure": [(ref, store.read(ref)) for ref in modalities["infrastructure"]]}
    rows, lookup, counts = business_logs(scope)
    results = []
    for group in correlation["groups"]:
        row = derive_group(store, scope, group, policy, as_of, rows, lookup, counts)
        row["rca_ref"] = store.write("manifests", row["rca"])
        results.append(row)
    return results


def incident_commands(consumer_id, closed, results, detected_at):
    commands = []
    for row in results:
        result = row["rca"]
        group = next(g for g in closed["correlation"]["groups"] if g["group_id"] == row["group_id"])
        identifier = "SLO-" + stable_id({"consumer_id": consumer_id, "group_id": group["group_id"]})
        detail = {"type": "timeline", "service": None, "observed_at": detected_at,
                  "source_ref": row["rca_ref"], "fact": "Continuous SLO consumer; analytical RCA hypotheses; actions_allowed=false; "
                  "IF/XGBoost unavailable; abstention=" + ",".join(result["abstention_reasons"])}
        detail["id"] = stable_id(detail)
        evidence = [*deepcopy(result["evidence"]), detail]
        candidates = {c["service"]: c for c in result["candidates"]}
        ranking = [{k: candidates[s][k] for k in ("service", "score", "evidence_refs")} for s in result["ranking"]]
        context = {"availability": "UNKNOWN", "recovery_source": None,
                   "source_refs": sorted({closed["scope_ref"], row["rca_ref"]}),
                   "config_refs": ["config://rca-policy/" + result["policy_sha256"]],
                   "trigger_sources": [{"source": "slo", "signal_id": item["signal"]["signal_id"],
                       "emitted_at": item["signal"]["evaluated_at"], "source_ref": item["signal_ref"]}
                       for item in closed["opening_objects"] if item["signal"]["signal_id"] in group["signal_ids"]]}
        for revision, stage in enumerate(("OPEN", "RCA_READY", "ESCALATED")):
            payload = {"schema_version": "1.0", "incident_id": identifier, "detected_at": detected_at,
                       "state": "escalated" if stage == "ESCALATED" else "incident", "severity": row["severity"],
                       "affected_services": group["affected_services"], "service_signals": [],
                       "root_cause_ranking": ranking if revision else [], "evidence": evidence}
            command = IncidentWrite(payload, stage, revision, identifier + ":" + stage,
                "continuous-incident-worker", "verified SLO source; analytical RCA; operator review required", deepcopy(context))
            command.validate()
            commands.append(command)
    return commands


class IncidentWorker:
    def __init__(self, pool, store: ArtifactStore, stream_id: str, topology: dict,
                 health_policy: dict, rca_policy: dict, *, evidence_source=None):
        validate_policy(rca_policy)
        topology_paths(topology)
        self.pool, self.store = pool, store
        self.topology, self.health_policy, self.rca_policy = deepcopy((topology, health_policy, rca_policy))
        self.reader = HealthLedgerReader(pool, store, stream_id, health_policy)
        self.repository = IncidentRepository(pool)
        self.identity = {"worker_version": "continuous-incidents/v1", "health_stream_id": stream_id,
                         "topology_sha256": stable_id(topology), "health_policy_sha256": stable_id(health_policy),
                         "rca_policy_sha256": stable_id(rca_policy),
                         "evidence_profile": evidence_source.profile if evidence_source else "slo-only/v1"}
        self.consumer_id = stable_id(self.identity)
        lock = int(self.consumer_id[:16], 16)
        self.lock_id = lock if lock < 2**63 else lock - 2**64
        self.evidence_source = evidence_source

    async def check_ready(self):
        await self.repository.check_ready()
        async with self.pool.acquire() as conn:
            if await conn.fetchval("SELECT array_agg(version ORDER BY version) FROM sentinel_control.schema_migrations") != [1, 2, 3]:
                raise ValueError("continuous incidents require explicit additive migration 3")
            if await conn.fetchval("""SELECT has_table_privilege('sentinel_control.incident_batches','UPDATE,DELETE,TRUNCATE')
                OR has_column_privilege('sentinel_control.incident_consumers','identity','UPDATE')"""):
                raise ValueError("consumer runtime can mutate accepted history or identity")

    async def status(self):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM sentinel_control.incident_consumers WHERE consumer_id=$1", self.consumer_id)
        return {"consumer_id": self.consumer_id, "last_bucket_end": timestamp(row["last_bucket_end"]) if row and row["last_bucket_end"] else None,
                "last_commit_ref": row["last_commit_ref"] if row else None}

    async def tick(self):
        started = time.monotonic()
        async with self.pool.acquire() as conn:
            if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", self.lock_id):
                return {"status": "BUSY", "consumer_id": self.consumer_id}
            try:
                return await self._locked_tick(conn, started)
            except asyncpg.InterfaceError:
                if conn.is_closed():
                    raise ConnectionError("incident database disconnected during processing") from None
                raise
            finally:
                if not conn.is_closed():
                    await conn.fetchval("SELECT pg_advisory_unlock($1)", self.lock_id)

    async def _locked_tick(self, conn, started):
        row = await conn.fetchrow("SELECT * FROM sentinel_control.incident_consumers WHERE consumer_id=$1", self.consumer_id)
        prior_ref = row["last_commit_ref"] if row else None
        checkpoint = None
        if row:
            if json.loads(row["identity"]) != self.identity:
                raise ValueError("consumer identity changed")
            if prior_ref:
                commit = self.store.read(prior_ref)
                checkpoint = commit["state"]
                accepted = await conn.fetchval("SELECT commit_ref FROM sentinel_control.incident_batches WHERE consumer_id=$1 AND bucket_end=$2",
                                               self.consumer_id, row["last_bucket_end"])
                if (accepted != prior_ref or commit["consumer_id"] != self.consumer_id
                    or checkpoint["last_bucket_end"] != timestamp(row["last_bucket_end"])):
                    raise ValueError("consumer checkpoint differs from immutable accepted history")
        source = await self.reader.next(checkpoint)
        if source is None:
            return {"status": "WAIT", "consumer_id": self.consumer_id}
        state, closed = await asyncio.to_thread(advance_groups, checkpoint, source["batch"],
            source["signal_objects"], self.topology, self.health_policy)
        modalities = [await asyncio.to_thread(self.evidence_source.capture, self.store, item) if self.evidence_source else None for item in closed]
        now = await conn.fetchval("SELECT clock_timestamp()")
        if now < utc_time(source["source_read_at"]):
            raise ValueError("processing clock precedes accepted source read")
        detected_at = timestamp(now)
        results, commands = [], []
        for item, evidence in zip(closed, modalities):
            rows = await asyncio.to_thread(evaluate_cluster, self.store, item, policy=self.rca_policy,
                as_of=now, health_policy=self.health_policy, modalities=evidence)
            results.extend(rows)
            commands.extend(incident_commands(self.consumer_id, item, rows, detected_at))
        commit = {"commit_version": "continuous-incidents/v1", "consumer_id": self.consumer_id,
                  "previous_commit_ref": prior_ref, "state": state, "source_batch_ref": state["last_batch_ref"],
                  "processed_at": detected_at, "closed_clusters": closed, "results": results,
                  "modalities": modalities,
                  "commands": [c.__dict__ for c in commands], "raw_health_lineage_verified": True,
                  "actions_allowed": False, "model_outputs_available": False}
        commit_ref = self.store.write("manifests", commit)
        async with conn.transaction():
            await conn.execute("""INSERT INTO sentinel_control.incident_consumers(consumer_id,stream_id,identity)
                VALUES ($1,$2,$3::jsonb) ON CONFLICT DO NOTHING""", self.consumer_id, self.reader.stream_id, json.dumps(self.identity))
            for command in commands:
                await self.repository.write_in_transaction(conn, command)
            await conn.execute("""INSERT INTO sentinel_control.incident_batches(consumer_id,stream_id,bucket_end,commit_ref)
                VALUES ($1,$2,$3,$4)""", self.consumer_id, self.reader.stream_id, utc_time(state["last_bucket_end"]), commit_ref)
            updated = await conn.execute("""UPDATE sentinel_control.incident_consumers SET last_bucket_end=$2,last_commit_ref=$3,
                updated_at=clock_timestamp() WHERE consumer_id=$1 AND last_commit_ref IS NOT DISTINCT FROM $4::text""",
                self.consumer_id, utc_time(state["last_bucket_end"]), commit_ref, prior_ref)
            if updated != "UPDATE 1":
                raise ValueError("consumer checkpoint compare-and-set failed")
        return {"status": "COMMITTED", "consumer_id": self.consumer_id, "bucket_end": state["last_bucket_end"],
                "commit_ref": commit_ref, "incidents": len(results), "revisions": len(commands),
                "processing_ms": round((time.monotonic() - started) * 1000, 3)}
