"""Bounded local experiments with truth kept outside serving captures."""
from __future__ import annotations

import asyncio
from collections import Counter, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from uuid import uuid4

import httpx

from sentinelops.experiments.client_clock import ClientClockGuard
from sentinelops.contracts import validate_payload
from sentinelops.experiments.labels import SLOConfig, measure_slo
from sentinelops.experiments.diagnostics import failure_context
from sentinelops.experiments import s4_postgres
from sentinelops.experiments.traffic import server_log_coverage,s1_error_responses,s5_expected_responses
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.infrastructure import PostgresInfrastructureSource
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import PrometheusSource, SOURCE_PROFILE, SOURCE_PROFILE_V2
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.telemetry.scheduling import capture_ready_bucket,wait_until_utc
from sentinelops.trace_query import decode_v3_trace
from sentinelops.trace_evidence import span_tags,validate_trace

CHECKOUT = "http://127.0.0.1:8001"
PAYMENT = "http://127.0.0.1:8002"
INVENTORY = "http://127.0.0.1:8003"


@dataclass(frozen=True)
class ExperimentConfig:
    scenario: str
    split: str
    seed: int = 42
    rps: float = 3.
    warmup_seconds: int = 300
    active_seconds: int = 300
    recovery_seconds: int = 180
    fault_mode: str = "latency"
    maximum_delay_ms: int = 1200
    target_service: str = "payment-service"
    all_entry_traffic: bool = False

    def validate(self):
        if any(type(value) is not int for value in (self.seed,self.warmup_seconds,self.active_seconds,self.recovery_seconds,self.maximum_delay_ms)):
            raise ValueError("seed, durations and fault delay must be integers")
        if self.scenario not in ("S0","S1","S3","S4","S5"):
            raise ValueError("runner supports S0/S1/S3/S4/S5; S2 requires its deployment adapter")
        if self.scenario=="S5" and self.split=="train":
            raise ValueError("S5 is RCA development/heldout evidence, not ML training data")
        if isinstance(self.rps,bool) or not isinstance(self.rps,(int,float)) or not math.isfinite(self.rps):
            raise ValueError("load rate must be finite numeric requests/second")
        if self.split not in ("train","validation","test") or self.seed < 0 or not 1 <= self.rps <= 10:
            raise ValueError("invalid declared split/seed/rate")
        if not 300 <= self.warmup_seconds <= 3600 or not 120 <= self.active_seconds <= 7200:
            raise ValueError("warmup must cover 300s; active duration must be 120…7200s")
        if self.scenario != "S0" and not 120 <= self.recovery_seconds <= 1800:
            raise ValueError("fault experiments require 120…1800s recovery")
        if self.fault_mode not in ("latency","error") or not 1 <= self.maximum_delay_ms <= 3000:
            raise ValueError("invalid sandbox fault bounds")
        if self.scenario == "S3" and self.fault_mode != "latency":
            raise ValueError("S3 requires gradual latency, not an abrupt error fault")
        if self.target_service not in ("payment-service", "inventory-service") or type(self.all_entry_traffic) is not bool:
            raise ValueError("invalid local fault target or entry traffic mode")
        if self.scenario == "S5" and self.target_service != "payment-service":
            raise ValueError("S5 retains its frozen independent Payment/Inventory targets")
        if self.scenario == "S4" and (self.split != "validation" or self.fault_mode != "latency"
                or self.target_service != "payment-service" or not 1000 <= self.maximum_delay_ms <= 1500
                or self.rps > 3 or not 180 <= self.active_seconds <= 600):
            raise ValueError("S4 requires validation, Payment latency, 1000–1500 ms pulses, at most 3 rps and 180–600 s active")


class ExperimentLock:
    def __init__(self, project: Path):
        self.path = project/"artifacts"/".experiment.lock"
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.stream = self.path.open("a+")
        try:
            fcntl.flock(self.stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.stream.close()
            raise RuntimeError("another local experiment holds the sandbox lock") from exc
        return self

    def __exit__(self,*args):
        fcntl.flock(self.stream,fcntl.LOCK_UN)
        self.stream.close()


def checkout_identity(project: Path) -> tuple[str,bool,dict]:
    commit = subprocess.check_output(["git","rev-parse","HEAD"],cwd=project,text=True).strip()
    dirty = bool(subprocess.check_output(["git","status","--porcelain"],cwd=project,text=True).strip())
    paths = {Path(module.__file__).resolve() for name,module in tuple(sys.modules.items())
             if name.startswith("sentinelops") and getattr(module,"__file__",None)}
    paths.update((project/"contracts").glob("*.schema.json"))
    paths.add(project/"scripts"/"run_experiment.py")
    paths.add(project/"configs"/"s5_development_protocol.json")
    hashes = {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()}
    return commit,dirty,hashes


def image_identity(project: Path) -> dict:
    images = {}
    for service in BUSINESS_ROUTES:
        identifier = subprocess.check_output(["docker","compose","ps","-q",service],cwd=project,text=True).strip()
        if not identifier:
            raise ValueError(f"required service is not running: {service}")
        images[service] = subprocess.check_output(["docker","inspect","--format","{{.Image}}",identifier],cwd=project,text=True).strip()
    return images


def serving_logs(project: Path, since: str) -> tuple[list[dict],list[dict]]:
    result = subprocess.run(["docker","compose","logs","--no-color","--no-log-prefix","--since",since,*BUSINESS_ROUTES],
                            cwd=project,capture_output=True,text=True,check=True,timeout=60)
    all_records, admitted = [], []
    business_events = {"payment_approved","inventory_read","checkout_completed","downstream_timeout","downstream_transport_error"}
    for line in result.stdout.splitlines():
        if not line.lstrip().startswith("{"):
            continue
        item = json.loads(line)
        all_records.append(item)
        service = item.get("service")
        if service not in BUSINESS_ROUTES:
            continue
        method,route = BUSINESS_ROUTES[service]
        event = item.get("event")
        completed = event == "request_completed" and item.get("route") == route and item.get("method") == method
        if completed or event in business_events:
            admitted.append(item)
    return all_records,admitted


def select_trace_probe(recent, entry, now, *, prefer_slow=False):
    eligible = (item for item in recent if item["entry_service"] == entry and now - item["completed_at"] >= 10)
    if prefer_slow:
        return max(eligible, key=lambda item: item["client_duration_ms"], default=None)
    return next(eligible, None)


async def _execute_locked(config: ExperimentConfig, *, project: Path, output: Path, session_id: str | None = None,
                  allow_dirty: bool = False, source_profile: str = SOURCE_PROFILE,
                  s4_lead_in: bool = False) -> Path:
    config.validate()
    commit,dirty,hashes = checkout_identity(project)
    if dirty and not allow_dirty:
        raise ValueError("commit source first; --allow-dirty creates non-training pilot evidence only")
    s4_project = s4_postgres.require_sandbox(project) if config.scenario == "S4" else None
    protocol_hash=None
    if config.scenario=="S5":
        try:protocol_hash=hashlib.sha256((project/"configs"/"s5_development_protocol.json").read_bytes()).hexdigest()
        except OSError as exc:raise ValueError("S5 development protocol must exist before capture") from exc
    images = image_identity(project)
    identifier = "run-"+uuid4().hex
    root = output/identifier
    root.mkdir(parents=True,exist_ok=False)
    serving,truth = ArtifactStore(root/"serving"),ArtifactStore(root/"truth")
    source = PrometheusSource()
    slo = SLOConfig()
    captures,infra_refs,trace_refs,outcomes = [],[],[],[]
    multi_entry = config.scenario == "S5" or config.all_entry_traffic
    recent = deque(maxlen=180 if multi_entry else 60)
    counts = Counter()
    ended = None
    originals = {}
    active = set()
    collector = lock_sampler = pulse = None
    lock_refs = []
    run = {
        "schema_version":"1.0","run_id":identifier,"session_id":session_id or "session-"+uuid4().hex,
        "scenario_id":config.scenario,"declared_split":config.split,"environment":"local","source_profile":source_profile,
        "seed":config.seed,"status":"running","source_commit":commit,"dirty_worktree":dirty,"image_digests":images,
        "started_at":timestamp(datetime.now(timezone.utc)),"ended_at":None,"warmup_end_at":None,"fault_start_at":None,
        "recovery_start_at":None,"root_cause_ground_truth":None if config.scenario in ("S0","S5") else config.target_service,
        "affected_services_ground_truth":[] if config.scenario == "S0" else sorted(BUSINESS_ROUTES) if config.scenario=="S5" else [config.target_service,"checkout-service"],
        "fault_method":"none" if config.scenario == "S0" else "multi-fault" if config.scenario=="S5" else ("gradual-latency" if config.scenario == "S3" else "sandbox-"+config.fault_mode),
        "slo_config_version":"slo/local-v1","label_protocol_version":"degradation-phase/v1",
        "slo_by_service":{service:{"first_violation_at":None,"confirmed_at":None,"request_count":0} for service in BUSINESS_ROUTES},
        "parameters":{**asdict(config),"slo":asdict(slo),"loaded_source_sha256":hashes},"phase_intervals":[],
        "source_manifest_ref":None,"logs_ref":None,"request_outcomes_ref":None,"infrastructure_refs":[],"failure_reason":None,
    }
    entries=tuple(BUSINESS_ROUTES) if multi_entry else ("checkout-service",)
    if multi_entry:
        run["parameters"]["traffic_plan"]={service:{"entry_rps":config.rps,"method":BUSINESS_ROUTES[service][0],
            "route":BUSINESS_ROUTES[service][1]} for service in entries}
    if config.scenario=="S5":
        run["root_causes_ground_truth"]=["payment-service","inventory-service"]
        run["parameters"]["s5_protocol_sha256"]=protocol_hash
        run["parameters"]["role"]="local-RCA-development-or-heldout-only"
    if s4_project:
        run.update(root_cause_ground_truth="rds", fault_method="db-pressure")
        run["parameters"]["s4_compose_project"] = s4_project
        run["parameters"]["s4_postgres_port"] = s4_postgres.database_port()
        run["parameters"]["s4_lock_injection"] = {"attempts": 0, "acquired": 0, "lock_timeouts": 0}
        run["parameters"]["s4_lock_gap_seconds"] = s4_postgres.PULSE_GAP_SECONDS
        s4_stages = s4_postgres.pressure_stages(config.active_seconds, config.maximum_delay_ms, s4_lead_in)
        if s4_lead_in:
            run["parameters"]["s4_pressure_stages"] = [
                {"phase": phase, "seconds": duration, "lock_hold_ms": delay}
                for phase, duration, delay in s4_stages
            ]
    failure = None
    client_clock = ClientClockGuard()
    async with httpx.AsyncClient(timeout=12.,trust_env=False) as client:
        async def set_fault(url,mode,delay=0):
            response = await client.post(url+"/internal/faults",json={"mode":mode,"delay_ms":delay})
            response.raise_for_status()

        semaphore = asyncio.Semaphore(16)
        rng = random.Random(config.seed)

        async def request(phase,entry="checkout-service"):
            async with semaphore:
                trace_id,correlation = uuid4().hex,"exp-"+uuid4().hex
                began = timestamp(datetime.now(timezone.utc))
                began_monotonic = time.perf_counter()
                try:
                    headers={"x-correlation-id":correlation,"traceparent":f"00-{trace_id}-{uuid4().hex[:16]}-01"}
                    if entry=="checkout-service":
                        response = await client.post(CHECKOUT+"/v1/checkout",json={"sku":"sku-001","quantity":1,"amount":9.9},headers=headers)
                    elif entry=="payment-service":
                        response = await client.post(PAYMENT+"/v1/payments/charge",json={"order_id":"direct-"+uuid4().hex,"amount":9.9,"currency":"USD"},headers=headers)
                    else:
                        response = await client.get(INVENTORY+"/v1/inventory/sku-001",headers=headers)
                    status = response.status_code
                    if response.headers.get("x-correlation-id") != correlation or response.headers.get("x-trace-id") != trace_id:
                        raise ValueError("request correlation/trace propagation failed")
                except (httpx.HTTPError,ValueError):
                    status = "transport_error"
                counts[str(status)] += 1
                outcome = {"phase":phase,"started_at":began,"completed_at":timestamp(datetime.now(timezone.utc)),
                           "status":status,"trace_id":trace_id,"correlation_id":correlation,"request_entry_service":entry,
                           "client_duration_ms":round((time.perf_counter()-began_monotonic)*1000, 3)}
                outcomes.append(outcome)
                client_clock.observe(outcome, run["started_at"])
                recent.append({"trace_id":trace_id,"completed_at":time.time(),"correlation_id":correlation,
                               "entry_service":entry,"client_duration_ms":outcome["client_duration_ms"]})

        def capture_trace(probe):
            trace_id=probe["trace_id"];entry=probe["entry_service"]
            response = source.client.get("http://127.0.0.1:16686/api/v3/traces/"+trace_id)
            response.raise_for_status()
            payload=decode_v3_trace(response.text,trace_id);decoded = payload["data"][0]
            services = {decoded["processes"][span["processID"]]["serviceName"] for span in decoded["spans"]
                        if any(tag["key"]=="span.kind" and tag["value"]=="server" for tag in span["tags"])}
            expected=set(BUSINESS_ROUTES) if entry=="checkout-service" else {entry}
            if not expected <= services:
                raise ValueError("incomplete trace snapshot")
            raw={"kind":"trace","trace_id":trace_id,"body":response.text,
                 "collected_at":timestamp(datetime.now(timezone.utc))}
            if entry!="checkout-service":
                statuses={entry:next(span_tags(span)["http.response.status_code"] for span in decoded["spans"]
                    if span_tags(span).get("span.kind")=="server")}
                validate_trace(payload,trace_id,probe["correlation_id"],statuses,require_timing=True,root_service=entry)
                raw["request_entry_service"]=entry
            return serving.write("raw",raw)

        async def collect():
            bucket = int(time.time()//60)*60+60
            while ended is None or bucket <= int(ended.timestamp()//60)*60:
                await wait_until_utc(bucket+30)
                if ended is not None and bucket > int(ended.timestamp()//60)*60:
                    break
                end = datetime.fromtimestamp(bucket,timezone.utc)
                for service in BUSINESS_ROUTES:
                    options = {"profile": source_profile} if source_profile != SOURCE_PROFILE else {}
                    captures.append(await capture_ready_bucket(source,service,end,store=serving,**options))
                if os.getenv("DATABASE_URL"):
                    item = await PostgresInfrastructureSource(os.environ["DATABASE_URL"]).capture()
                    infra_refs.append(await asyncio.to_thread(serving.write,"observations",item))
                for entry in entries:
                    probe = select_trace_probe(recent, entry, time.time(), prefer_slow=bool(s4_project))
                    if probe:
                        try:
                            trace_refs.append(await asyncio.to_thread(capture_trace,probe))
                        except (httpx.HTTPError,ValueError):
                            run["parameters"]["unavailable_trace_snapshots"] = run["parameters"].get("unavailable_trace_snapshots",0)+1
                print(f"CAPTURE {identifier} {timestamp(end)} outcomes={dict(counts)}",flush=True)
                bucket += 60

        async def sample_locks():
            observer = PostgresInfrastructureSource(s4_postgres.database_url())
            identity = None
            while True:
                item = await observer.capture_lock_waits()
                current = item["database_oid"], item["postgres_started_at"]
                if identity is not None and current != identity:
                    raise ValueError("S4 PostgreSQL source changed during collection")
                identity = current
                lock_refs.append(serving.write("raw", item))
                await asyncio.sleep(1)

        async def load(phase,duration,ramp=False):
            begin = datetime.now(timezone.utc)
            deadline = time.monotonic()+duration
            scheduled,next_ramp = time.monotonic(),time.monotonic()
            print(f"PHASE {identifier} {phase} duration={duration}s split={config.split}",flush=True)
            while time.monotonic() < deadline:
                client_clock.check()
                for background in (collector, lock_sampler, pulse):
                    if background and background.done():
                        background.result()
                if ramp and time.monotonic() >= next_ramp:
                    progress = max(.05,1-(deadline-time.monotonic())/duration)
                    target = PAYMENT if config.target_service == "payment-service" else INVENTORY
                    await set_fault(target,"latency",max(1,int(config.maximum_delay_ms*progress)))
                    next_ramp += 30
                if len(active)+len(entries) > 32:
                    raise RuntimeError("bounded workload queue saturated; lower load or fault delay")
                for entry in entries:
                    task = asyncio.create_task(request(phase,entry));active.add(task);task.add_done_callback(active.discard)
                scheduled += (1+rng.uniform(-.05,.05))/config.rps
                scheduled = max(scheduled,time.monotonic())
                await asyncio.sleep(max(0.,scheduled-time.monotonic()))
            if active: await asyncio.gather(*tuple(active))
            client_clock.check()
            run["phase_intervals"].append({"phase":phase,"start_at":timestamp(begin),"end_at":timestamp(datetime.now(timezone.utc))})
            truth.write("manifests",run)

        try:
            run["parameters"]["source_configuration"] = await asyncio.to_thread(source.verify_configuration)
            for url in (CHECKOUT,PAYMENT,INVENTORY):
                (await client.get(url+"/ready")).raise_for_status()
            for url in (PAYMENT,INVENTORY):
                response = await client.get(url+"/internal/faults");response.raise_for_status();originals[url]=response.json()
            await set_fault(INVENTORY,"healthy");await set_fault(PAYMENT,"healthy")
            run["started_at"] = timestamp(datetime.now(timezone.utc))
            validate_payload("experiment-run.schema.json",run)
            truth.write("manifests",run)  # Split and parameters are committed to truth before load.
            collector = asyncio.create_task(collect())
            if s4_project:
                lock_sampler = asyncio.create_task(sample_locks())
            await load("warmup",config.warmup_seconds)
            run["warmup_end_at"] = timestamp(datetime.now(timezone.utc))
            if config.scenario == "S0":
                await load("healthy",config.active_seconds)
            elif s4_project:
                run["fault_start_at"] = timestamp(datetime.now(timezone.utc))
                for phase, duration, delay in s4_stages:
                    pulse = asyncio.create_task(s4_postgres.lock_pulses(delay / 1000, project, s4_project,
                        stats=run["parameters"]["s4_lock_injection"]))
                    try:
                        await load(phase, duration)
                    finally:
                        pulse.cancel()
                        try:
                            await pulse
                        except asyncio.CancelledError:
                            pass
                        pulse = None
                run["recovery_start_at"] = timestamp(datetime.now(timezone.utc))
                await load("recovery", config.recovery_seconds)
            else:
                mode = "latency" if config.scenario == "S3" else config.fault_mode
                target = PAYMENT if config.target_service == "payment-service" else INVENTORY
                await set_fault(target,mode,1 if config.scenario == "S3" else config.maximum_delay_ms)
                if config.scenario=="S5":await set_fault(INVENTORY,mode,config.maximum_delay_ms)
                run["fault_start_at"] = timestamp(datetime.now(timezone.utc))
                await load("degrading" if config.scenario == "S3" else "fault",config.active_seconds,config.scenario == "S3")
                await set_fault(target,"healthy")
                if config.scenario=="S5":await set_fault(INVENTORY,"healthy")
                run["recovery_start_at"] = timestamp(datetime.now(timezone.utc))
                await load("recovery",config.recovery_seconds)
            ended = datetime.now(timezone.utc)
            await collector
        except BaseException as exc:
            failure = type(exc).__name__
            run["parameters"]["failure_context"] = failure_context(exc,project)
            ended = datetime.now(timezone.utc)
        finally:
            if lock_sampler:
                lock_sampler.cancel()
                try:
                    await lock_sampler
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    failure = failure or type(exc).__name__
            if collector and not collector.done():
                collector.cancel();await asyncio.gather(collector,return_exceptions=True)
            if active: await asyncio.gather(*tuple(active),return_exceptions=True)
            if failure == "ClientClockDiscontinuity":
                ended = datetime.now(timezone.utc)
            for url,original in originals.items():
                try: await set_fault(url,original["mode"],original["delay_ms"])
                except Exception as exc:
                    failure = "FaultRestoreFailed"
                    run["parameters"].setdefault("restore_failure_context",[]).append(failure_context(exc,project))
            source.close()
    run["ended_at"] = timestamp(ended or datetime.now(timezone.utc))
    try:
        full,admitted = serving_logs(project,run["started_at"])
        full_logs_ref=truth.write("raw",{"kind":"full-experiment-logs","records":full})
        run["logs_ref"] = serving.write("raw",{"kind":"serving-logs","records":admitted,
                                              "collected_at":timestamp(datetime.now(timezone.utc))})
        run["request_outcomes_ref"] = truth.write("raw",{"outcomes":outcomes})
        correlations = {item["correlation_id"] for item in outcomes}
        if any(item["correlation_id"] not in correlations for item in admitted if item["event"] == "request_completed"):
            failure = failure or "UnexpectedBusinessTraffic"
        if multi_entry:
            coverage=server_log_coverage(full,admitted,outcomes)
            run["parameters"].update(full_logs_ref=full_logs_ref,server_log_coverage=coverage)
            if not all(row["complete"] for row in coverage.values()):failure=failure or "IncompleteServerLogs"
        for service in BUSINESS_ROUTES:
            measured = measure_slo(admitted,service,slo,start_at=datetime.fromisoformat(run["started_at"].replace("Z","+00:00")),end_at=ended)
            run["slo_by_service"][service] = {key:measured[key] for key in ("first_violation_at","confirmed_at","request_count")}
            if not multi_entry and measured["request_count"] < .95*len(outcomes): failure = failure or "IncompleteServerLogs"
        base = process_captures(captures,serving,default_profile=source_profile)
        if base["eligible_feature_count"] == 0:
            failure = failure or "NoEligibleFeatureWindows"
        manifest = {key:value for key,value in base.items() if key != "manifest_ref"}
        manifest["normalization_manifest_ref"] = base["manifest_ref"]
        manifest["evidence_refs"] = {"logs":run["logs_ref"],"traces":trace_refs,"infrastructure":infra_refs}
        if s4_project:
            manifest["postgres_lock_wait_refs"] = lock_refs
            lock_logs = await s4_postgres.capture_lock_logs(project, s4_project, run["started_at"], run["ended_at"])
            if lock_logs is not None:
                if not lock_refs:
                    raise ValueError("S4 lock logs require a matching PostgreSQL observation")
                snapshot = serving.read(lock_refs[0])
                identity_keys = ("source_ref", "postgres_started_at", "database_oid")
                if any(lock_logs[key] != snapshot[key] for key in identity_keys):
                    raise ValueError("S4 lock logs belong to a different PostgreSQL instance")
                manifest["postgres_lock_wait_log_ref"] = serving.write("raw", lock_logs)
            if not any(serving.read(ref)["waiting_clients"] for ref in lock_refs):
                failure = failure or "NoObservedPostgresLockWait"
        run["source_manifest_ref"] = serving.write("manifests",manifest)
        run["infrastructure_refs"] = infra_refs
        run["parameters"]["workload_outcomes"] = dict(counts)
        if config.scenario=="S5":
            if not s5_expected_responses(outcomes,config.fault_mode):failure=failure or "UnexpectedWorkloadOutcome"
        elif any(key not in ("200","502","504","503") for key in counts) or (counts.get("503") and not s1_error_responses(outcomes,config)):
            failure = failure or "UnexpectedWorkloadOutcome"
        if config.scenario == "S0" and any(value["first_violation_at"] for value in run["slo_by_service"].values()): failure = failure or "HealthyRunBreachedSLO"
    except Exception as exc:
        failure = failure or type(exc).__name__
        run["parameters"]["evidence_failure_context"] = failure_context(exc,project)
    run["dirty_worktree"] = dirty or any(not Path(path).is_file() or hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest for path,digest in hashes.items())
    run["status"],run["failure_reason"] = ("failed",failure) if failure else ("completed",None)
    validate_payload("experiment-run.schema.json",run)
    truth.write("manifests",run)
    (root/"run.json").write_text(json.dumps(run,indent=2))
    print(f"EXPERIMENT {run['status'].upper()} {identifier} output={root} reason={failure}",flush=True)
    if failure:
        raise RuntimeError(f"experiment failed ({failure}); partial evidence saved in {root}")
    return root


async def execute(config: ExperimentConfig, *, project: Path, output: Path, session_id: str | None = None,
                  allow_dirty: bool = False, source_profile: str = SOURCE_PROFILE,
                  s4_lead_in: bool = False) -> Path:
    config.validate()
    if type(s4_lead_in) is not bool or (s4_lead_in and config.scenario != "S4"):
        raise ValueError("the lock lead-in is only available for S4 development")
    if s4_lead_in:
        stages = s4_postgres.pressure_stages(config.active_seconds, config.maximum_delay_ms, lead_in=True)
        if config.warmup_seconds + sum(seconds for _, seconds, _ in stages) + config.recovery_seconds > 6000:
            raise ValueError("S4 with a lock lead-in must fit the bounded snapshot budget")
    if config.scenario == "S4" and (allow_dirty or os.getenv("DATABASE_URL") not in (None, s4_postgres.database_url())):
        raise ValueError("S4 requires clean source and the dedicated Compose PostgreSQL connection")
    if source_profile not in (SOURCE_PROFILE, SOURCE_PROFILE_V2):
        raise ValueError("unsupported experiment source profile")
    if source_profile == SOURCE_PROFILE_V2 and (
        config.split != "validation" or config.scenario not in ("S0", "S1", "S3", "S4")
        or config.warmup_seconds < 1800 or not config.all_entry_traffic or allow_dirty
    ):
        raise ValueError("ADR-016 capture requires clean S0/S1/S3/S4 validation with all-entry traffic and 30-minute warmup")
    with ExperimentLock(project):
        return await _execute_locked(config, project=project, output=output, session_id=session_id,
                                     allow_dirty=allow_dirty, source_profile=source_profile, s4_lead_in=s4_lead_in)
