"""Native acceptance observer; controls stay outside the incident consumer.

The observer owns process crashes and compares DB/API/artifact evidence. The
consumer receives only its accepted health stream and operational source config.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID

import yaml

from sentinelops.telemetry.core import timestamp
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health_source import HealthLedgerReader
from sentinelops.workers.incident_journal import IncidentJournal
from sentinelops.workers.incident_state import advance_groups


class IncidentWorkerEvidence:
    def __init__(self, output: Path, source_store: ArtifactStore, stream_id: str, project: str):
        self.output, self.source_store, self.stream_id, self.project = output, source_store, stream_id, project
        self.output.mkdir(parents=True, exist_ok=False)
        self.store = ArtifactStore(output / "objects")
        self.children, self.logs, self.handles, self.interventions = [], [], [], []
        self.consumer_id = None
        self.crashed = False
        self.created = time.monotonic()
        self.protocol = json.loads(Path("configs/incident_worker_acceptance.json").read_text())
        if (self.protocol["protocol_version"] != "continuous-incident-acceptance/v1"
            or self.protocol["s1_protocol"] != "configs/health_worker_acceptance.json"
            or self.protocol["s5_protocol"] != "configs/s5_development_protocol.json"
            or self.protocol["required_lifecycle"] != ["OPEN", "RCA_READY", "ESCALATED", "CLOSED"]
            or self.protocol["required_final_state"] != "recovered"
            or self.protocol["actions_allowed"] is not False or self.protocol["heldout_evaluation"] is not False
            or self.protocol["full_G4_accepted"] is not False or self.protocol["training_corpus"] is not False):
            raise ValueError("unsupported continuous incident acceptance protocol")

    def _save(self):
        (self.output / "running.json").write_text(json.dumps({"stream_id": self.stream_id,
            "consumer_id": self.consumer_id, "child_pids": [p.pid for p in self.children],
            "processes": [{"pid": p.pid, "exit_code": p.poll()} for p in self.children],
            "interventions": self.interventions, "native_project": self.project}, indent=2) + "\n")

    def spawn(self):
        path = self.output / ("consumer-" + str(len(self.children)) + ".log")
        handle = path.open("x")
        process = subprocess.Popen([sys.executable, "-m", "scripts.run_incident_worker", "--health-stream", self.stream_id,
            "--source-store", str(self.source_store.root), "--store", str(self.store.root), "--project", self.project],
            stdout=handle, stderr=subprocess.STDOUT, env=os.environ.copy())
        self.children.append(process); self.logs.append(path); self.handles.append(handle); self._save()
        return process

    async def observe(self, pool):
        for index, process in enumerate(self.children):
            if self.crashed and index == 0:
                assert process.returncode == -9
            elif process.poll() is not None:
                raise AssertionError("continuous incident process exited before native acceptance completed")
        for path in self.logs:
            for line in path.read_text().splitlines():
                if line.startswith("{") and (row := json.loads(line)).get("status") == "READY":
                    if self.consumer_id is not None and self.consumer_id != row["consumer_id"]:
                        raise AssertionError("restarted incident process changed consumer identity")
                    self.consumer_id = row["consumer_id"]
        if self.consumer_id is None:
            if time.monotonic() - self.created > 30:
                raise AssertionError("continuous incident process did not initialize within its startup bound")
            return
        async with pool.acquire() as conn:
            count = await conn.fetchval("""SELECT count(*) FROM sentinel_control.incident_jobs j
                JOIN sentinel_control.incidents i USING(incident_id) WHERE j.consumer_id=$1""", self.consumer_id)
        if count and not self.crashed:
            import asyncio
            primary = self.children[0]
            primary.kill()
            assert await asyncio.to_thread(primary.wait, timeout=10) == -9
            self.crashed = True
            self.interventions.append({"kind": "incident-consumer-SIGKILL-after-durable-IER", "pid": primary.pid,
                                       "at": timestamp(datetime.now(timezone.utc))})
            self.spawn(); self.spawn()
        self._save()

    async def require_closed(self, pool, *, timeout=45.):
        import asyncio
        deadline = time.monotonic() + timeout
        while True:
            await self.observe(pool)
            if self.consumer_id:
                async with pool.acquire() as conn:
                    row = await conn.fetchrow("""SELECT count(*) AS jobs,count(*) FILTER(WHERE i.lifecycle_stage='CLOSED') AS closed
                        FROM sentinel_control.incident_jobs j JOIN sentinel_control.incidents i USING(incident_id)
                        WHERE j.consumer_id=$1""", self.consumer_id)
                if row["jobs"] and row["jobs"] == row["closed"]:
                    return
            if time.monotonic() >= deadline:
                raise AssertionError("native incident lifecycle has not naturally closed; do not replace UNKNOWN with recovery")
            await asyncio.sleep(.5)

    async def stop(self):
        import asyncio
        for process in self.children:
            if process.poll() is None:
                process.terminate()
                code = await asyncio.to_thread(process.wait, timeout=50)
                assert code == 0
        for handle in self.handles:
            handle.close()
        self._save()

    async def catch_up(self, pool, *, timeout=45.):
        import asyncio
        deadline = time.monotonic() + timeout
        while True:
            await self.observe(pool)
            async with pool.acquire() as conn:
                ready = await conn.fetchval("""SELECT s.health_batch_ref=h.last_batch_ref
                    FROM sentinel_control.incident_consumer_checkpoints p
                    JOIN sentinel_control.incident_consumers c USING(consumer_id)
                    JOIN sentinel_control.health_streams h ON h.stream_id=c.health_stream_id
                    LEFT JOIN sentinel_control.incident_consumer_steps s
                        ON s.consumer_id=p.consumer_id AND s.state_ref=p.last_state_ref
                    WHERE p.consumer_id=$1""", self.consumer_id)
            if ready:
                return
            if time.monotonic() >= deadline:
                raise AssertionError("incident consumer has not caught up to the final accepted health watermark")
            await asyncio.sleep(.5)

    async def verify(self, pool, *, scenario: str) -> dict:
        if not self.consumer_id or not self.crashed:
            raise AssertionError("native incident process restart was not exercised")
        async with pool.acquire() as conn:
            consumer = await conn.fetchrow("SELECT * FROM sentinel_control.incident_consumers WHERE consumer_id=$1", self.consumer_id)
            source = await conn.fetchrow("SELECT * FROM sentinel_control.health_streams WHERE stream_id=$1", self.stream_id)
        identity = json.loads(consumer["identity"])
        reader = HealthLedgerReader(pool, self.source_store, self.stream_id, json.loads(Path("configs/slo_health_policy.json").read_text()))
        journal = IncidentJournal(pool, reader, self.store, yaml.safe_load(Path("configs/topology.yaml").read_text()),
            json.loads(Path("configs/rca_policy.json").read_text()), identity["evidence_profile"])
        _, checkpoint = await journal.checkpoint()
        assert checkpoint["last_batch_ref"] == source["last_batch_ref"]
        state, source_groups, source_batches = None, set(), 0
        while (verified := await reader.next(state)) is not None:
            state, closed = advance_groups(state, verified["batch"], verified["signal_objects"], journal.topology, reader.policy)
            source_groups.update(group["group_id"] for scope in closed for group in scope["correlation"]["groups"])
            source_batches += 1
            assert source_batches <= 10000
        assert state == {k: v for k, v in checkpoint.items() if k != "source_ledger_view_ref"}
        async with pool.acquire() as conn:
            jobs = await conn.fetch("SELECT * FROM sentinel_control.incident_jobs WHERE consumer_id=$1 ORDER BY job_id", self.consumer_id)
            assert {job["group_id"] for job in jobs} == source_groups
            snapshot = {"consumer": dict(consumer), "jobs": [dict(j) for j in jobs]}
            for table in ("incident_consumer_steps", "incident_consumer_checkpoints", "incident_episode_recoveries"):
                snapshot[table] = [dict(r) for r in await conn.fetch("SELECT * FROM sentinel_control." + table + " WHERE consumer_id=$1", self.consumer_id)]
        members, summaries = [], []
        for job in jobs:
            verified_job, evidence, derived = await journal.verified_evidence(job["job_id"])
            result = derived["rca"]
            group = next(g for g in result["correlation"]["groups"] if g["group_id"] == job["group_id"])
            members.append(tuple(group["affected_services"]))
            current = await journal.repository.get(job["incident_id"])
            history = await journal.repository.history(job["incident_id"])
            assert [r["lifecycle_stage"] for r in history] == self.protocol["required_lifecycle"]
            assert current["revision"] == 4 and current["payload"]["state"] == "recovered"
            assert result["abstained"] and result["actions_allowed"] is False
            assert all(c["components"]["anomaly_strength"]["value"] is None for c in result["candidates"] if c["service"] != "rds")
            for revision in range(1, 5):
                stored = await journal.repository.get(job["incident_id"], revision)
                assert stored["payload"]["detected_at"] == timestamp(job["detected_at"]) and stored["payload"]["service_signals"] == []
            recovery = await journal.verified_recovery(dict(job))
            assert recovery and {r["signal_ref"] for r in recovery} <= set(current["context"]["source_refs"])
            assert current["context"]["availability"] == "ELIGIBLE" and current["context"]["recovery_source"] == "natural"
            views = [self.store.read(ref) for ref in current["context"]["source_refs"] if ref.startswith("artifact://manifests/")]
            views = [v for v in views if v.get("view_version") == "accepted-health-read-view/v1"]
            latest = max(views, key=lambda v: v["accepted_row"]["bucket_end"])
            states = [self.store.read(ref) for ref in latest["signal_refs"]]
            assert all(s["availability"] == "ELIGIBLE" for s in states if s["service"] in group["affected_services"])
            capture = self.store.read(evidence["capture_ref"])
            logs = self.store.read(capture["evidence_refs"]["logs"])
            assert logs["records"] and capture["evidence_refs"]["traces"]
            if group["grouping_reason"] == "ambiguous-shared-consumer":
                assert "ambiguous-correlation" in result["abstention_reasons"]
            async with pool.acquire() as conn:
                tables = {}
                for table in ("incident_job_evidence", "incident_commands"):
                    tables[table] = [dict(r) for r in await conn.fetch("SELECT * FROM sentinel_control." + table + " WHERE job_id=$1", job["job_id"])]
                tables["incident_command_receipts"] = [dict(r) for r in await conn.fetch("""SELECT r.* FROM sentinel_control.incident_command_receipts r
                    JOIN sentinel_control.incident_commands c USING(command_id) WHERE c.job_id=$1""", job["job_id"])]
                for table in ("incidents", "incident_versions", "incident_events"):
                    tables[table] = [dict(r) for r in await conn.fetch("SELECT * FROM sentinel_control." + table + " WHERE incident_id=$1", job["incident_id"])]
                tables["outbox"] = [dict(r) for r in await conn.fetch("SELECT * FROM sentinel_control.outbox WHERE body->>'incident_id'=$1", job["incident_id"])]
            assert all(len(tables[t]) == 4 for t in ("incident_commands", "incident_command_receipts", "incident_versions", "incident_events", "outbox"))
            snapshot[job["job_id"]] = tables
            summaries.append({"job_id": job["job_id"], "incident_id": job["incident_id"], "members": group["affected_services"],
                "capture_ref": evidence["capture_ref"], "result_ref": evidence["result_ref"], "rca_abstained": True,
                "source_log_completions": len(logs["records"]), "captured_traces": len(capture["evidence_refs"]["traces"]),
                "natural_recovery_verified": True, "versions": 4})
        if scenario not in ("S1", "S5"):
            raise ValueError("continuous incident acceptance requires its declared S1/S5 scenario")
        expected = {tuple(members) for members in self.protocol["s1_group_members" if scenario == "S1" else "s5_group_members"]}
        assert set(members) == expected and len(members) == len(expected)
        def encoded(value):
            if isinstance(value, datetime): return timestamp(value)
            if isinstance(value, UUID): return str(value)
            raise TypeError("unsupported acceptance snapshot value")
        blob = json.dumps(snapshot, indent=2, default=encoded) + "\n"
        (self.output / "database-snapshot.json").write_text(blob)
        report = {"status": "PASS", "mode": "actual-clock-native-continuous-incident-integration", "scenario": scenario,
            "consumer_id": self.consumer_id, "stream_id": self.stream_id, "jobs": summaries, "interventions": self.interventions,
            "database_snapshot_sha256": hashlib.sha256(blob.encode()).hexdigest(), "actions_allowed": False,
            "raw_health_and_group_prefix_recomputed": True, "source_batches": source_batches,
            "full_G4_accepted": False, "training_corpus": False, "heldout_evaluation": False}
        (self.output / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
