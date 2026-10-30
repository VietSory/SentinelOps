"""Native acceptance observer; controls stay outside the incident consumer.

The observer owns process crashes and compares DB/API/artifact evidence. The
consumer receives only its accepted health stream and operational source config.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import signal
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
from sentinelops.workers.health import local_endpoint
from scripts.incident_metrics_evidence import IncidentMetricEvidence, validate_protocol


def verify_scenario_group(scenario: str, group: dict, result: dict) -> None:
    """Require the declared S5 ambiguity, including when a wrong label hides it."""
    if scenario not in ("S1", "S5"):
        raise ValueError("continuous incident acceptance requires its declared S1/S5 scenario")
    if group["grouping_reason"] == "ambiguous-shared-consumer" and "ambiguous-correlation" not in result["abstention_reasons"]:
        raise ValueError("ambiguous source group must retain RCA ambiguity abstention")
    if scenario == "S5":
        members = group["affected_services"]
        if members == ["checkout-service"]:
            if (group["grouping_reason"] != "ambiguous-shared-consumer"
                or group["structural_candidates"] != ["inventory-service", "payment-service"]
                or "ambiguous-correlation" not in result["abstention_reasons"]):
                raise ValueError("S5 Checkout must preserve both independent roots and shared-consumer ambiguity")
        elif members in (["inventory-service"], ["payment-service"]):
            if group["grouping_reason"] != "single-service" or group["structural_candidates"] != members:
                raise ValueError("S5 independent root must remain a separate single-service candidate")
        else:
            raise ValueError("S5 continuous acceptance must retain three separate source groups")


class IncidentWorkerEvidence:
    def __init__(self, output: Path, source_store: ArtifactStore, stream_id: str, project: str, *, metrics_endpoint=None):
        if metrics_endpoint and local_endpoint(metrics_endpoint) != 'http://127.0.0.1:4317':
            raise ValueError('native incident metrics require the owned loopback Collector')
        self.output, self.source_store, self.stream_id, self.project = output, source_store, stream_id, project
        self.output.mkdir(parents=True, exist_ok=False)
        self.store = ArtifactStore(output / "objects")
        self.children, self.logs, self.handles, self.interventions = [], [], [], []
        self.consumer_id = None
        self.crashed = False
        self.created = time.monotonic()
        self.metrics_endpoint = metrics_endpoint
        self.metrics = IncidentMetricEvidence(output / 'own-metrics') if metrics_endpoint else None
        self.metrics_initialized = False
        self.paused = []
        self.metrics_probe = None
        if self.metrics:
            self.metrics_protocol = json.loads(Path('configs/incident_metrics_acceptance.json').read_text())
            validate_protocol(self.metrics_protocol)
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
        command = [sys.executable, "-m", "scripts.run_incident_worker", "--health-stream", self.stream_id,
            "--source-store", str(self.source_store.root), "--store", str(self.store.root), "--project", self.project]
        if self.metrics:
            assert len(self.children) < 3
            command += ['--metrics-otlp-endpoint', self.metrics_endpoint,
                        '--metrics-instance', 'incidents-0' if len(self.children) < 2 else 'incidents-1']
        process = subprocess.Popen(command,
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
        if self.metrics and not self.metrics_initialized:
            await self.metrics.ready('primary-ready', instances={'incidents-0'}, value=1)
            self.metrics_initialized = True
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
            if self.metrics:
                await self.metrics.restarted()
        self._save()

    def live_metric_slots(self):
        return {'incidents-0' if i < 2 else 'incidents-1' for i, p in enumerate(self.children) if p.poll() is None}

    async def metrics_outage(self, pool, collector):
        """Task-owned control probe, after natural closure, outside worker inputs."""
        import asyncio
        if not self.metrics:
            return
        info = json.loads(await asyncio.to_thread(subprocess.check_output, ['docker', 'inspect', collector],
                                                  stderr=subprocess.DEVNULL, timeout=30))[0]
        labels = info['Config']['Labels']
        assert labels.get('com.docker.compose.project') == self.project
        assert labels.get('com.docker.compose.service') == 'otel-collector' and info['State']['Running']
        assert any(p['HostIp'] == '127.0.0.1' and p['HostPort'] == '4317'
                   for p in (info['NetworkSettings']['Ports'].get('4317/tcp') or []))

        async def counts():
            async with pool.acquire() as conn:
                health = await conn.fetchval('SELECT count(*) FROM sentinel_control.health_batches WHERE stream_id=$1', self.stream_id)
                steps = await conn.fetchval('SELECT count(*) FROM sentinel_control.incident_consumer_steps WHERE consumer_id=$1', self.consumer_id)
            return {'health': health, 'steps': steps}

        async def docker(action):
            result = await asyncio.to_thread(subprocess.run, ['docker', action, collector],
                                            capture_output=True, timeout=60)
            assert result.returncode == 0, 'owned Collector control failed'

        await self.catch_up(pool)
        down = False
        try:
            for process in self.children:
                if process.poll() is None:
                    process.send_signal(signal.SIGSTOP)
                    self.paused.append(process)
            self.interventions.append({'kind': 'metrics-consumers-SIGSTOP', 'at': timestamp(datetime.now(timezone.utc))})
            self._save()
            baseline = await counts()
            await asyncio.sleep(self.metrics_protocol['backlog_pause_seconds'])
            backlog = await counts()
            assert backlog['steps'] == baseline['steps']
            assert backlog['health'] - baseline['health'] >= self.metrics_protocol['minimum_backlog_steps']
            # Resume while export is unavailable: source/journal progress must
            # not depend on a successful metrics RPC.
            down = True
            await docker('stop')
            self.interventions.append({'kind': 'metrics-collector-stopped', 'at': timestamp(datetime.now(timezone.utc))})
            for process in self.paused:
                assert process.poll() is None
                process.send_signal(signal.SIGCONT)
            self.paused.clear()
            self._save()
            await asyncio.sleep(self.metrics_protocol['collector_outage_seconds'])
            during = await counts()
            assert during['steps'] - backlog['steps'] >= self.metrics_protocol['minimum_backlog_steps']
            assert all(p.poll() is None for p in self.children[1:])
            await docker('start')
            down = False
            self.interventions.append({'kind': 'metrics-collector-started', 'at': timestamp(datetime.now(timezone.utc))})
            self._save()
            await self.metrics.ready('collector-recovered', instances=self.live_metric_slots(), value=1)
            self.metrics_probe = {'status': 'PASS', 'baseline': baseline, 'paused_backlog': backlog,
                                  'while_export_unavailable': during, 'source_progress_without_export': True}
            (self.output / 'own-metrics/backlog-exporter-probe.json').write_text(json.dumps(self.metrics_probe, indent=2) + '\n')
        finally:
            for process in self.paused:
                if process.poll() is None:
                    process.send_signal(signal.SIGCONT)
            self.paused.clear()
            if down:
                await docker('start')

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
        for process in self.paused:
            if process.poll() is None:
                process.send_signal(signal.SIGCONT)
        self.paused.clear()
        running = [process for process in self.children if process.poll() is None]
        for process in running:
            process.terminate()
        codes = await asyncio.gather(*(asyncio.to_thread(process.wait, timeout=50) for process in running))
        assert all(code == 0 for code in codes)
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
        metrics_result = None
        metric_refs = None
        if self.metrics:
            # Collect the final fresh shutdown sample before the potentially
            # expensive immutable raw replay; stopped processes cannot refresh it.
            async with pool.acquire() as conn:
                rows = await conn.fetch('SELECT state_ref FROM sentinel_control.incident_consumer_steps WHERE consumer_id=$1', self.consumer_id)
            metric_refs = {r['state_ref'] for r in rows}
            metrics_result = await self.metrics.final(self.logs, self.store, accepted_refs=metric_refs)
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
            verify_scenario_group(scenario, group, result)
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
                "grouping_reason": group["grouping_reason"], "structural_candidates": group["structural_candidates"],
                "abstention_reasons": result["abstention_reasons"],
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
        if self.metrics:
            assert self.metrics_probe and self.metrics_probe['status'] == 'PASS'
            counts = [sum(e['status'] == 'INGESTED' for e in row.get('events', []))
                      for path in self.logs for line in path.read_text().splitlines()
                      if line.startswith('{') and (row := json.loads(line)).get('status') == 'PROGRESS']
            assert max(counts) >= self.metrics_protocol['minimum_backlog_steps'], 'actual catch-up cycle was not exercised'
            assert metric_refs == {row['state_ref'] for row in snapshot['incident_consumer_steps']}
            report['own_metrics_verification'] = metrics_result
            report['own_metrics_verification']['maximum_actual_ingested_per_cycle'] = max(counts)
            report['own_metrics_verification']['protocol_sha256'] = hashlib.sha256(Path('configs/incident_metrics_acceptance.json').read_bytes()).hexdigest()
        (self.output / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
