"""Durable continuous-consumer cursor, frozen intents and publication receipts."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.incidents.domain import IncidentConflict, IncidentWrite
from sentinelops.incidents.correlation import topology_paths
from sentinelops.incidents.rca import validate_policy as validate_rca_policy
from sentinelops.incidents.rca_replay import group_severity
from sentinelops.incidents.repository import IncidentRepository
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health_source import HealthLedgerReader, decoded, at, validate_stream
from sentinelops.workers.incident_state import advance_groups
from sentinelops.workers.incident_rca import derive_continuous

ACTOR = "continuous-incident-worker"
HISTORY_TABLES = ("incident_consumers", "incident_consumer_steps", "incident_jobs",
                  "incident_episode_recoveries", "incident_job_evidence", "incident_commands",
                  "incident_command_receipts")


def episode_key(stream_id: str, signal: dict) -> str:
    return stable_id({"health_stream_id": stream_id, "service": signal["service"],
                      "episode_start_at": signal["episode_start_at"]})


class IncidentJournal:
    def __init__(self, pool, source: HealthLedgerReader, store: ArtifactStore, topology: dict,
                 rca_policy: dict, evidence_profile: dict):
        validate_rca_policy(rca_policy)
        if not set(BUSINESS_ROUTES) <= set(topology_paths(topology)):
            raise ValueError("continuous incident topology omits a monitored source service")
        self.pool, self.source, self.store = pool, source, store
        self.topology = json.loads(json.dumps(topology, allow_nan=False))
        self.rca_policy = json.loads(json.dumps(rca_policy, allow_nan=False))
        self.consumer_id = stable_id({"consumer_version": "continuous-incident/v1", "health_stream_id": source.stream_id})
        self.identity = {"consumer_version": "continuous-incident/v1", "health_stream_id": source.stream_id,
            "health_policy_sha256": stable_id(source.policy), "topology_sha256": stable_id(self.topology),
            "rca_policy_sha256": stable_id(self.rca_policy), "artifact_root_hash": stable_id({"root": str(store.root)}),
            "evidence_profile": json.loads(json.dumps(evidence_profile, allow_nan=False))}
        self.repository = IncidentRepository(pool)

    async def initialize(self):
        await self.repository.check_ready()
        async with self.pool.acquire() as conn, conn.transaction():
            if await conn.fetchval("SELECT array_agg(version ORDER BY version) FROM sentinel_control.schema_migrations") != [1, 2, 3]:
                raise ValueError("continuous incident consumer requires explicit control migration 3")
            for table in HISTORY_TABLES:
                if await conn.fetchval("SELECT has_table_privilege($1,'UPDATE,DELETE,TRUNCATE')", "sentinel_control." + table):
                    raise ValueError("incident consumer runtime can mutate accepted history")
                if not (await conn.fetchval("SELECT has_table_privilege($1,'SELECT')", "sentinel_control." + table)
                        and await conn.fetchval("SELECT has_table_privilege($1,'INSERT')", "sentinel_control." + table)):
                    raise ValueError("incident consumer runtime lacks required journal access")
            stream = await conn.fetchrow("SELECT * FROM sentinel_control.health_streams WHERE stream_id=$1", self.source.stream_id)
            if stream is None:
                raise ValueError("initialize the selected accepted health stream before its consumer")
            source_identity = validate_stream(stream, self.source.policy)
            if source_identity["artifact_root_hash"] != stable_id({"root": str(self.source.store.root)}):
                raise ValueError("consumer source store does not match its selected health stream")
            await conn.execute("""INSERT INTO sentinel_control.incident_consumers(consumer_id,health_stream_id,identity)
                VALUES($1,$2,$3::jsonb) ON CONFLICT(consumer_id) DO NOTHING""",
                self.consumer_id, self.source.stream_id, json.dumps(self.identity))
            identity = await conn.fetchval("SELECT identity FROM sentinel_control.incident_consumers WHERE consumer_id=$1", self.consumer_id)
            if decoded(identity) != self.identity:
                raise ValueError("consumer configuration changed; explicit migration is required")
            await conn.execute("""INSERT INTO sentinel_control.incident_consumer_checkpoints(consumer_id)
                VALUES($1) ON CONFLICT DO NOTHING""", self.consumer_id)

    async def checkpoint(self) -> tuple[str | None, dict | None]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""SELECT c.identity,p.last_state_ref,s.bucket_end,s.health_batch_ref,
                (SELECT max(bucket_end) FROM sentinel_control.incident_consumer_steps WHERE consumer_id=$1) AS latest
                FROM sentinel_control.incident_consumers c
                JOIN sentinel_control.incident_consumer_checkpoints p USING(consumer_id)
                LEFT JOIN sentinel_control.incident_consumer_steps s
                    ON s.consumer_id=p.consumer_id AND s.state_ref=p.last_state_ref WHERE c.consumer_id=$1""", self.consumer_id)
        if row is None or decoded(row["identity"]) != self.identity or row["latest"] != row["bucket_end"]:
            raise ValueError("consumer checkpoint is missing, rewound or configured differently")
        ref = row["last_state_ref"]
        if ref is None:
            return None, None
        state = self.store.read(ref)
        if (state["state_version"] != "continuous-correlation-state/v1"
            or state["last_bucket_end"] != timestamp(row["bucket_end"])
            or state["last_batch_ref"] != row["health_batch_ref"]
            or state["source"]["health_stream_id"] != self.source.stream_id
            or state["source"]["topology_sha256"] != self.identity["topology_sha256"]):
            raise ValueError("consumer checkpoint artifact differs from its accepted step")
        view = self.store.read(state["source_ledger_view_ref"])
        if (view["view_version"] != "accepted-health-read-view/v1"
            or view["source_stream"]["stream_id"] != self.source.stream_id
            or view["accepted_row"]["manifest_ref"] != row["health_batch_ref"]
            or at(view["accepted_row"]["bucket_end"]) != row["bucket_end"]
            or view["health_policy"] != self.source.policy):
            raise ValueError("consumer checkpoint's accepted source read view differs")
        return ref, state

    def _archive_openings(self, closed: dict):
        for item in closed["opening_objects"]:
            signal = item["signal"]
            if self.store.write("signals", signal) != item["signal_ref"]:
                raise ValueError("opening signal changed during consumer archival")
            for ref in signal["evidence_refs"]:
                observation = self.source.store.read(ref)
                if self.store.write("observations", observation) != ref:
                    raise ValueError("opening observation changed during archival")
                for raw_ref in observation["provenance"]["source_refs"]:
                    if self.store.write("raw", self.source.store.read(raw_ref)) != raw_ref:
                        raise ValueError("opening raw source changed during archival")

    def _archive_verified(self, verified: dict) -> str:
        batch = verified["batch"]
        refs = {batch["normalization_manifest_ref"], *(ref for ref, _ in verified["signal_objects"])}
        for key in ("raw_refs", "observation_refs", "feature_refs", "quarantine_refs"):
            refs.update(verified["normalization"][key])
        for ref in sorted(refs):
            body = self.source.store.read(ref)
            if self.store.write(ref.split('/')[2], body) != ref:
                raise ValueError("verified source object changed during archival")
        if self.store.write("manifests", batch) != verified["accepted_row"]["manifest_ref"]:
            raise ValueError("accepted source batch changed during archival")
        def encoded(value):
            if isinstance(value, datetime): return timestamp(value)
            raise TypeError("unsupported source ledger metadata")
        view = {"view_version": "accepted-health-read-view/v1", "source_stream": verified["source_stream"],
                "accepted_row": verified["accepted_row"], "source_read_at": verified["source_read_at"],
                "signal_refs": batch["signal_refs"], "health_policy": self.source.policy}
        return self.store.write("manifests", json.loads(json.dumps(view, default=encoded, allow_nan=False)))

    def _open_command(self, intent_ref: str, intent: dict, closed: dict, group: dict, current: dict) -> IncidentWrite:
        evidence = []
        for item in closed["opening_objects"]:
            signal = item["signal"]
            if signal["signal_id"] not in group["signal_ids"]:
                continue
            fact = {"type": "timeline", "service": signal["service"], "observed_at": signal["episode_start_at"],
                "source_ref": item["signal_ref"], "fact": "Verified SLO onset " + signal["episode_start_at"]
                + "; actual health decision clock " + signal["evaluated_at"] + "; not a historical alert-notification time"}
            evidence.append({"id": stable_id({"group_id": group["group_id"], **fact}), **fact})
        clock = {"type": "timeline", "service": None, "observed_at": intent["detected_at"], "source_ref": intent_ref,
                 "fact": "Actual local consumer intent-preparation clock; original source onset/evaluation clocks remain separate"}
        evidence.append({"id": stable_id(clock), **clock})
        signals = {item["signal"]["signal_id"]: (item["signal_ref"], item["signal"]) for item in closed["opening_objects"]}
        payload = {"schema_version": "1.0", "incident_id": intent["incident_id"], "detected_at": intent["detected_at"],
            "state": "incident", "severity": group_severity(self.store, group, signals),
            "affected_services": group["affected_services"], "service_signals": [], "root_cause_ranking": [], "evidence": evidence}
        context = {"availability": "ELIGIBLE" if all(current[s]["availability"] == "ELIGIBLE" for s in group["affected_services"]) else "UNKNOWN",
            "recovery_source": None, "source_refs": sorted({intent_ref, closed["scope_ref"], intent["correlation_ref"],
                *(item["signal_ref"] for item in closed["opening_objects"]),
                *("artifact://signals/" + stable_id(current[s]) + ".json" for s in group["affected_services"])}),
            "config_refs": ["config://health-policy/" + self.identity["health_policy_sha256"],
                            "config://rca-policy/" + self.identity["rca_policy_sha256"]],
            "trigger_sources": [{"source": "slo", "signal_id": key, "emitted_at": intent["detected_at"],
                "source_ref": next(item["signal_ref"] for item in closed["opening_objects"] if item["signal"]["signal_id"] == key)}
                for key in group["signal_ids"]]}
        identifier = stable_id({"job_id": intent["job_id"], "expected_revision": 0})
        command = IncidentWrite(payload, "OPEN", 0, identifier, ACTOR, "source-verified continuous SLO group; RCA pending", context)
        command.validate()
        return command

    def freeze_command(self, command: IncidentWrite) -> tuple[str, str]:
        command = IncidentWrite(**json.loads(json.dumps(command.__dict__, allow_nan=False)))
        command.validate()
        if (command.actor != ACTOR or command.payload["service_signals"]
            or any(t["source"] != "slo" for t in command.context["trigger_sources"])
            or command.context["recovery_source"] not in (None, "natural")
            or command.lifecycle_stage not in ("OPEN", "RCA_READY", "ESCALATED", "VERIFYING", "RECOVERED", "CLOSED")
            or command.lifecycle_stage == "OPEN" and command.payload["root_cause_ranking"]):
            raise ValueError("continuous SLO publication cannot fabricate model signals or change actor")
        return self.store.write("manifests", command.__dict__), command.fingerprint()

    async def verified_recovery(self, job: dict) -> list[dict] | None:
        intent = self.store.read(job["intent_ref"])
        correlation = self.store.read(intent["correlation_ref"])
        validate_payload("correlation-result.schema.json", correlation)
        group = next(g for g in correlation["groups"] if g["group_id"] == intent["group_id"])
        members = [item["signal"] for item in intent["opening_objects"] if item["signal"]["signal_id"] in group["signal_ids"]]
        expected = {episode_key(self.source.stream_id, signal): signal for signal in members}
        async with self.pool.acquire() as conn:
            records = await conn.fetch("""SELECT r.*,s.payload AS source_payload,s.stream_id AS source_stream_id
                FROM sentinel_control.incident_episode_recoveries r
                JOIN sentinel_control.health_signals s ON s.signal_id=r.signal_id
                WHERE r.consumer_id=$1 AND r.episode_key=ANY($2::text[])""", self.consumer_id, sorted(expected))
        if len(records) != len(expected):
            return None
        recovered = []
        for record in records:
            signal = self.store.read(record["signal_ref"])
            validate_payload("health-signal.schema.json", signal)
            opening = expected[record["episode_key"]]
            if (signal != decoded(record["source_payload"]) or record["source_stream_id"] != self.source.stream_id
                or signal["signal_id"] != record["signal_id"] or record["service"] != opening["service"]
                or signal["service"] != opening["service"] or signal["episode_start_at"] != opening["episode_start_at"]
                or signal["policy_sha256"] != self.identity["health_policy_sha256"]
                or signal["event"] != "recovery_confirmed" or signal["availability"] != "ELIGIBLE"
                or signal["incident_active"] or signal["health"] != "healthy"
                or signal["clear_streak"] != self.source.policy["recovery_buckets"]):
                raise ValueError("accepted recovery does not resolve the same verified source episode")
            recovered.append({"signal_ref": record["signal_ref"], "signal": signal})
        return recovered

    async def ingest_next(self) -> dict:
        prior_ref, previous = await self.checkpoint()
        verified = await self.source.next(previous)
        if verified is None:
            return {"status": "WAIT", "consumer_id": self.consumer_id}
        state, closed_scopes = advance_groups(previous, verified["batch"], verified["signal_objects"], self.topology, self.source.policy)
        state["source_ledger_view_ref"] = self._archive_verified(verified)
        state_ref = self.store.write("manifests", state)
        # Read a real intent clock, then perform file I/O before opening the write tx.
        async with self.pool.acquire() as conn:
            detected = await conn.fetchval("SELECT clock_timestamp()")
        jobs = []
        for closed in closed_scopes:
            self._archive_openings(closed)
            if self.store.write("manifests", closed["scope"]) != closed["scope_ref"]:
                raise ValueError("correlation scope changed during archival")
            correlation_ref = self.store.write("manifests", closed["correlation"])
            if detected < utc_time(closed["correlation"]["as_of"]):
                raise ValueError("consumer intent clock precedes source decisions")
            for group in closed["correlation"]["groups"]:
                job_id = stable_id({"consumer_id": self.consumer_id, "group_id": group["group_id"]})
                intent = {"intent_version": "continuous-incident-intent/v1", "job_id": job_id,
                    "consumer_id": self.consumer_id, "incident_id": "INC-" + group["group_id"],
                    "group_id": group["group_id"], "detected_at": timestamp(detected),
                    "scope_ref": closed["scope_ref"], "correlation_ref": correlation_ref,
                    "opening_objects": closed["opening_objects"], "rca_policy": self.rca_policy,
                    "actions_allowed": False, "historical_emissions_verified": False}
                intent_ref = self.store.write("manifests", intent)
                command = self._open_command(intent_ref, intent, closed, group, state["service_states"])
                command_ref, fingerprint = self.freeze_command(command)
                jobs.append((intent, intent_ref, command, command_ref, fingerprint))
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("""INSERT INTO sentinel_control.incident_consumer_steps
                (consumer_id,bucket_end,health_batch_ref,state_ref) VALUES($1,$2,$3,$4)""",
                self.consumer_id, utc_time(state["last_bucket_end"]), state["last_batch_ref"], state_ref)
            for ref, signal in verified["signal_objects"]:
                if signal["event"] == "recovery_confirmed":
                    await conn.execute("""INSERT INTO sentinel_control.incident_episode_recoveries
                        (consumer_id,episode_key,service,episode_start_at,bucket_end,signal_id,signal_ref)
                        VALUES($1,$2,$3,$4,$5,$6,$7)""", self.consumer_id, episode_key(self.source.stream_id, signal),
                        signal["service"], utc_time(signal["episode_start_at"]), utc_time(signal["bucket_end"]), signal["signal_id"], ref)
            for intent, intent_ref, command, command_ref, fingerprint in jobs:
                await conn.execute("""INSERT INTO sentinel_control.incident_jobs
                    (job_id,consumer_id,group_id,closing_bucket_end,incident_id,intent_ref,detected_at)
                    VALUES($1,$2,$3,$4,$5,$6,$7)""", intent["job_id"], self.consumer_id, intent["group_id"],
                    utc_time(state["last_bucket_end"]), intent["incident_id"], intent_ref, detected)
                await self._insert_command(conn, intent["job_id"], command, command_ref, fingerprint)
            updated = await conn.execute("""UPDATE sentinel_control.incident_consumer_checkpoints
                SET last_state_ref=$2,updated_at=clock_timestamp()
                WHERE consumer_id=$1 AND last_state_ref IS NOT DISTINCT FROM $3::text""", self.consumer_id, state_ref, prior_ref)
            if updated != "UPDATE 1":
                raise IncidentConflict("consumer checkpoint changed during processing")
        return {"status": "INGESTED", "consumer_id": self.consumer_id, "bucket_end": state["last_bucket_end"],
                "state_ref": state_ref, "created_jobs": [intent["job_id"] for intent, *_ in jobs]}

    async def _insert_command(self, conn, job_id, command, ref, fingerprint):
        await conn.execute("""INSERT INTO sentinel_control.incident_commands
            (command_id,job_id,incident_id,expected_revision,lifecycle_stage,command_ref,request_hash)
            VALUES($1,$2,$3,$4,$5,$6,$7)""", command.idempotency_key, job_id, command.payload["incident_id"],
            command.expected_revision, command.lifecycle_stage, ref, fingerprint)

    async def _verified_step(self, bucket_end) -> tuple[dict, list[dict]]:
        async with self.pool.acquire() as conn:
            step = await conn.fetchrow("""SELECT s.*,p.state_ref AS previous_state_ref
                FROM sentinel_control.incident_consumer_steps s
                LEFT JOIN sentinel_control.incident_consumer_steps p
                    ON p.consumer_id=s.consumer_id AND p.bucket_end=s.bucket_end-interval '60 seconds'
                WHERE s.consumer_id=$1 AND s.bucket_end=$2""", self.consumer_id, at(bucket_end))
        if step is None:
            raise ValueError("incident job has no accepted consumer step")
        previous = self.store.read(step["previous_state_ref"]) if step["previous_state_ref"] else None
        verified = await self.source.next(previous)
        if (verified is None or verified["accepted_row"]["manifest_ref"] != step["health_batch_ref"]
            or at(verified["batch"]["bucket_end"]) != step["bucket_end"]):
            raise ValueError("incident step differs from its verified accepted health source")
        state, closed = advance_groups(previous, verified["batch"], verified["signal_objects"], self.topology, self.source.policy)
        archived = self.store.read(step["state_ref"])
        if {k: v for k, v in archived.items() if k != "source_ledger_view_ref"} != state:
            raise ValueError("accepted incident state does not reproduce from its source step")
        return verified, closed

    async def verified_job(self, job_id: str) -> tuple[dict, dict, dict]:
        """Recheck closing membership and every opening's raw source lineage.

        The immutable accepted journal is the authority for the preceding state;
        bounded rechecks do not replay an ever-growing stream prefix per job.
        SQL snapshots are released before file/source verification.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM sentinel_control.incident_jobs WHERE job_id=$1 AND consumer_id=$2",
                                      job_id, self.consumer_id)
        if row is None:
            raise ValueError("RCA has no accepted consumer job")
        job = dict(row)
        intent = self.store.read(job["intent_ref"])
        if (intent["intent_version"] != "continuous-incident-intent/v1" or intent["job_id"] != job_id
            or intent["consumer_id"] != self.consumer_id or intent["incident_id"] != job["incident_id"]
            or intent["group_id"] != job["group_id"] or intent["detected_at"] != timestamp(job["detected_at"])
            or intent["rca_policy"] != self.rca_policy or intent["actions_allowed"] is not False
            or intent["historical_emissions_verified"] is not False):
            raise ValueError("RCA intent differs from its immutable accepted consumer job")
        _, scopes = await self._verified_step(job["closing_bucket_end"])
        matches = [closed for closed in scopes if closed["scope_ref"] == intent["scope_ref"]]
        if len(matches) != 1:
            raise ValueError("incident job scope does not reproduce at its accepted closing watermark")
        closed = matches[0]
        if (closed["opening_objects"] != intent["opening_objects"]
            or self.store.read(intent["correlation_ref"]) != closed["correlation"]
            or self.store.read(intent["scope_ref"]) != closed["scope"]
            or not any(group["group_id"] == job["group_id"] for group in closed["correlation"]["groups"])):
            raise ValueError("incident membership/identity changed from its accepted source scope")
        for end in sorted({item["signal"]["bucket_end"] for item in closed["opening_objects"]}):
            verified, _ = await self._verified_step(end)
            actual = {ref: signal for ref, signal in verified["signal_objects"]}
            if any(actual.get(item["signal_ref"]) != item["signal"] for item in closed["opening_objects"]
                   if item["signal"]["bucket_end"] == end):
                raise ValueError("accepted incident opening does not reproduce from its raw source")
        return job, intent, closed

    async def enrich_one(self, collector) -> dict | None:
        if collector.profile != self.identity["evidence_profile"]:
            raise ValueError("operational evidence profile changed; explicit consumer migration is required")
        async with self.pool.acquire() as conn:
            selected = await conn.fetchrow("""SELECT j.job_id FROM sentinel_control.incident_jobs j
                LEFT JOIN sentinel_control.incident_job_evidence e USING(job_id)
                WHERE j.consumer_id=$1 AND e.job_id IS NULL ORDER BY j.closing_bucket_end,j.job_id LIMIT 1""", self.consumer_id)
        if selected is None:
            return None
        job, intent, closed = await self.verified_job(selected["job_id"])
        group = next(g for g in closed["correlation"]["groups"] if g["group_id"] == job["group_id"])
        _, checkpoint = await self.checkpoint()
        health_identity = checkpoint["source"]["health_identity"]
        capture_ref = await collector.capture(self.store, job_id=job["job_id"], intent_ref=job["intent_ref"],
            group=group, policy=self.rca_policy, health_identity=health_identity,
            latency_ms=self.source.policy["latency_ms"])
        derived = await asyncio.to_thread(derive_continuous, self.store, intent_ref=job["intent_ref"],
            intent=intent, closed=closed, capture_ref=capture_ref, profile=collector.profile,
            health_identity=health_identity, health_policy=self.source.policy)
        result_ref = self.store.write("manifests", derived["rca"])
        async with self.pool.acquire() as conn, conn.transaction():
            current = await conn.fetchval("SELECT clock_timestamp()")
            if utc_time(derived["rca"]["as_of"]) > current:
                raise ValueError("actual RCA analysis clock is ahead of its control database receipt")
            await conn.execute("""INSERT INTO sentinel_control.incident_job_evidence(job_id,capture_ref,result_ref,analyzed_at)
                VALUES($1,$2,$3,$4) ON CONFLICT(job_id) DO NOTHING""", job["job_id"], capture_ref, result_ref,
                utc_time(derived["rca"]["as_of"]))
            accepted = await conn.fetchrow("SELECT * FROM sentinel_control.incident_job_evidence WHERE job_id=$1", job["job_id"])
        return {"status": "ENRICHED" if accepted["capture_ref"] == capture_ref else "ALREADY_ENRICHED",
                "job_id": job["job_id"], "capture_ref": accepted["capture_ref"], "result_ref": accepted["result_ref"]}

    async def verified_evidence(self, job_id: str) -> tuple[dict, dict, dict]:
        job, intent, closed = await self.verified_job(job_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM sentinel_control.incident_job_evidence WHERE job_id=$1", job_id)
        if row is None:
            raise ValueError("derived incident revision requires accepted source-derived RCA evidence")
        _, checkpoint = await self.checkpoint()
        derived = await asyncio.to_thread(derive_continuous, self.store, intent_ref=job["intent_ref"],
            intent=intent, closed=closed, capture_ref=row["capture_ref"], profile=self.identity["evidence_profile"],
            health_identity=checkpoint["source"]["health_identity"], health_policy=self.source.policy)
        if (self.store.read(row["result_ref"]) != derived["rca"]
            or timestamp(row["analyzed_at"]) != derived["rca"]["as_of"]):
            raise ValueError("accepted RCA result does not reproduce from its frozen operational evidence")
        return job, dict(row), derived

    async def project_one(self) -> dict | None:
        """Advance one source-derived lifecycle step; never execute an action."""
        async with self.pool.acquire() as conn:
            jobs = await conn.fetch("""SELECT j.job_id,j.incident_id,i.lifecycle_stage FROM sentinel_control.incident_jobs j
                JOIN sentinel_control.incident_job_evidence e USING(job_id)
                JOIN sentinel_control.incidents i USING(incident_id)
                WHERE j.consumer_id=$1 AND i.lifecycle_stage<>'CLOSED'
                AND NOT EXISTS (SELECT 1 FROM sentinel_control.incident_commands c
                    LEFT JOIN sentinel_control.incident_command_receipts r USING(command_id)
                    WHERE c.job_id=j.job_id AND r.command_id IS NULL)
                ORDER BY j.closing_bucket_end,j.job_id LIMIT 101""", self.consumer_id)
        if len(jobs) > 100:
            raise ValueError("continuous lifecycle pending-job budget exceeded; do not silently starve later jobs")
        _, current = await self.checkpoint()
        for selected in jobs:
            job, evidence, derived = await self.verified_evidence(selected["job_id"])
            result = derived["rca"]
            stored = await self.repository.get(job["incident_id"])
            stage = stored["context"]["lifecycle_stage"]
            if stage != selected["lifecycle_stage"]:
                raise IncidentConflict("incident lifecycle changed during trusted projection")
            recovery = None
            available = all(current["service_states"][s]["availability"] == "ELIGIBLE"
                            for s in stored["payload"]["affected_services"])
            if stage == "OPEN":
                target = "RCA_READY"
            elif stage == "RCA_READY" and result["abstained"]:
                target = "ESCALATED"
            else:
                if not available:
                    continue
                recovery = await self.verified_recovery(job)
                if recovery is None:
                    continue
                target = {"RCA_READY": "VERIFYING", "VERIFYING": "RECOVERED",
                          "RECOVERED": "CLOSED", "ESCALATED": "CLOSED"}.get(stage)
                if target is None:
                    raise ValueError("continuous incident projection encountered an unsupported external action stage")
            payload = json.loads(json.dumps(stored["payload"], allow_nan=False))
            facts = {f["id"]: f for f in payload["evidence"]}
            facts.update({f["id"]: f for f in result["evidence"]})
            detail = {"type": "timeline", "service": None, "observed_at": result["as_of"],
                "source_ref": evidence["result_ref"], "fact": "Source-verified continuous RCA hypotheses; actions_allowed=false; abstention="
                + ",".join(result["abstention_reasons"])}
            detail["id"] = stable_id(detail)
            facts[detail["id"]] = detail
            payload["evidence"] = sorted(facts.values(), key=lambda f: f["id"])
            candidates = {c["service"]: c for c in result["candidates"]}
            payload["root_cause_ranking"] = [{k: candidates[s][k] for k in ("service", "score", "evidence_refs")}
                                             for s in result["ranking"]]
            payload["severity"] = derived["severity"]
            payload["state"] = {"RCA_READY": "incident", "ESCALATED": "escalated", "VERIFYING": "recovering",
                                  "RECOVERED": "recovered", "CLOSED": "recovered"}[target]
            context = {k: v for k, v in stored["context"].items() if k != "lifecycle_stage"}
            context["availability"] = "ELIGIBLE" if available else "UNKNOWN"
            current_refs = {"artifact://signals/" + stable_id(current["service_states"][s]) + ".json"
                            for s in payload["affected_services"]}
            context["source_refs"] = sorted(set(context["source_refs"]) | current_refs
                | {evidence["capture_ref"], evidence["result_ref"], current["source_ledger_view_ref"]})
            if recovery is not None:
                context["recovery_source"] = "natural"
                context["source_refs"] = sorted(set(context["source_refs"]) | {r["signal_ref"] for r in recovery})
                for item in recovery:
                    signal = item["signal"]
                    fact = {"type": "timeline", "service": signal["service"], "observed_at": signal["evaluated_at"],
                        "source_ref": item["signal_ref"], "fact": "Matching source episode recovered after "
                        + str(self.source.policy["recovery_buckets"]) + " eligible clear buckets; actual source evaluation clock; no action executed"}
                    fact["id"] = stable_id({"group_id": job["group_id"], **fact})
                    facts[fact["id"]] = fact
                payload["evidence"] = sorted(facts.values(), key=lambda f: f["id"])
            return await self.queue_revision(job["job_id"], payload, context, target,
                "source-verified RCA abstention; no action authority" if target == "ESCALATED" else
                "source-verified natural episode recovery" if recovery is not None else "source-derived RCA evidence ready")
        return None

    async def queue_revision(self, job_id: str, payload: dict, context: dict, stage: str, reason: str) -> dict:
        """Internal trusted projection path; no public arbitrary computed-write API."""
        frozen = json.loads(json.dumps({"payload": payload, "context": context}, allow_nan=False))
        async with self.pool.acquire() as conn:
            job = await conn.fetchrow("SELECT * FROM sentinel_control.incident_jobs WHERE job_id=$1 AND consumer_id=$2", job_id, self.consumer_id)
            if job is None:
                raise ValueError("incident revision has no accepted consumer intent")
            pending = await conn.fetchrow("""SELECT c.command_id,c.lifecycle_stage FROM sentinel_control.incident_commands c
                LEFT JOIN sentinel_control.incident_command_receipts r USING(command_id)
                WHERE c.job_id=$1 AND r.command_id IS NULL ORDER BY c.expected_revision LIMIT 1""", job_id)
            if pending:
                return {"status": "PENDING", **dict(pending)}
            revision = await conn.fetchval("""SELECT COALESCE(max(r.revision),0) FROM sentinel_control.incident_command_receipts r
                JOIN sentinel_control.incident_commands c USING(command_id) WHERE c.job_id=$1""", job_id)
        if (frozen["payload"]["incident_id"] != job["incident_id"]
            or frozen["payload"]["detected_at"] != timestamp(job["detected_at"]) or revision < 1):
            raise ValueError("continuous revision changed the accepted identity/clock or skipped OPEN")
        _, evidence, derived = await self.verified_evidence(job_id)
        result = derived["rca"]
        if (result["group_id"] != job["group_id"] or result["policy"] != self.rca_policy
            or result["actions_allowed"] is not False):
            raise ValueError("accepted RCA evidence belongs to another group/policy")
        candidates = {c["service"]: c for c in result["candidates"]}
        ranking = [{key: candidates[service][key] for key in ("service", "score", "evidence_refs")}
                   for service in result["ranking"]]
        facts = {fact["id"]: fact for fact in frozen["payload"]["evidence"]}
        if (frozen["payload"]["root_cause_ranking"] != ranking
            or any(facts.get(fact["id"]) != fact for fact in result["evidence"])):
            raise ValueError("incident projection changed accepted RCA ranking or evidence")
        if stage in ("VERIFYING", "RECOVERED", "CLOSED"):
            recovery = await self.verified_recovery(job)
            _, checkpoint = await self.checkpoint()
            current_available = all(checkpoint["service_states"][s]["availability"] == "ELIGIBLE"
                for s in frozen["payload"]["affected_services"])
            if (recovery is None or frozen["context"]["recovery_source"] != "natural"
                or not current_available
                or frozen["context"]["availability"] != "ELIGIBLE"
                or stage == "CLOSED" and frozen["payload"]["state"] != "recovered"):
                raise ValueError("automatic closure/verification needs all matching source recoveries")
            if not {item["signal_ref"] for item in recovery} <= set(frozen["context"]["source_refs"]):
                raise ValueError("natural recovery projection omits its verified source references")
            current_refs = {checkpoint["source_ledger_view_ref"], *(
                "artifact://signals/" + stable_id(checkpoint["service_states"][s]) + ".json"
                for s in frozen["payload"]["affected_services"])}
            if not current_refs <= set(frozen["context"]["source_refs"]):
                raise ValueError("natural recovery projection omits its accepted current availability snapshot")
        command_id = stable_id({"job_id": job_id, "expected_revision": revision})
        command = IncidentWrite(frozen["payload"], stage, revision, command_id, ACTOR, reason, frozen["context"])
        ref, fingerprint = self.freeze_command(command)
        async with self.pool.acquire() as conn, conn.transaction():
            await self._insert_command(conn, job_id, command, ref, fingerprint)
        return {"status": "QUEUED", "command_id": command_id, "lifecycle_stage": stage}

    async def publish_one(self) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""SELECT c.*,j.intent_ref,j.detected_at FROM sentinel_control.incident_commands c
                JOIN sentinel_control.incident_jobs j USING(job_id)
                LEFT JOIN sentinel_control.incident_command_receipts r USING(command_id)
                WHERE j.consumer_id=$1 AND r.command_id IS NULL ORDER BY c.recorded_at,c.expected_revision,c.command_id LIMIT 1""", self.consumer_id)
        if row is None:
            return None
        command = IncidentWrite(**self.store.read(row["command_ref"]))
        intent = self.store.read(row["intent_ref"])
        if (command.actor != ACTOR or command.fingerprint() != row["request_hash"]
            or command.idempotency_key != row["command_id"] or command.expected_revision != row["expected_revision"]
            or command.lifecycle_stage != row["lifecycle_stage"] or command.payload["incident_id"] != row["incident_id"]
            or command.payload["detected_at"] != timestamp(row["detected_at"])
            or intent["incident_id"] != row["incident_id"] or intent["consumer_id"] != self.consumer_id):
            raise ValueError("frozen incident command differs from its accepted job")
        stored = await self.repository.write(command)
        async with self.pool.acquire() as conn:
            await conn.execute("""INSERT INTO sentinel_control.incident_command_receipts(command_id,incident_id,revision)
                SELECT $1,v.incident_id,v.revision FROM sentinel_control.incident_versions v
                WHERE v.incident_id=$2 AND v.revision=$3 AND v.request_hash=$4 AND v.idempotency_key=$1
                ON CONFLICT(command_id) DO NOTHING""", command.idempotency_key, row["incident_id"], stored["revision"], row["request_hash"])
            receipt = await conn.fetchrow("SELECT * FROM sentinel_control.incident_command_receipts WHERE command_id=$1", command.idempotency_key)
        if receipt is None or (receipt["incident_id"], receipt["revision"]) != (stored["incident_id"], stored["revision"]):
            raise IncidentConflict("incident commit has no matching immutable publication receipt")
        return {"status": "PUBLISHED", "incident_id": stored["incident_id"], "revision": stored["revision"],
                "command_id": command.idempotency_key}
