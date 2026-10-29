"""Bounded read-only local business evidence, separate from experiment controls.

Ownership is checked before collection. Missing/unsafe/oversized sources produce
explicit unavailability; they never become healthy samples or action authority.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

import httpx
import asyncpg
import yaml

from sentinelops.contracts import utc_time
from sentinelops.incidents.rca import validate_policy
from sentinelops.incidents.rca_source import epoch_ns, reject_control
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.infrastructure import PostgresInfrastructureSource
from sentinelops.trace_query import decode_v3_trace

LOG_BYTES = 4 * 1024 * 1024
TRACE_BYTES = 512 * 1024
LOG_RECORDS = 25000
TRACES_PER_ENTRY = 3
LOG_FIELDS = {"timestamp_unix_ms", "service", "event", "correlation_id", "request_instance_id",
              "trace_id", "span_id", "method", "path", "route", "status_code", "duration_ms"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def local_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
        or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
        or parsed.path not in ("", "/") or not parsed.port):
        raise ValueError("native evidence endpoint requires an explicit loopback HTTP port")
    return value.rstrip("/")


async def docker_read(*args: str, limit: int = LOG_BYTES, timeout: float = 8.) -> bytes:
    """Read a bounded combined Docker stream; never retain an oversized prefix."""
    process = await asyncio.create_subprocess_exec("docker", *args, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
    try:
        async with asyncio.timeout(timeout):
            chunks, size = [], 0
            while chunk := await process.stdout.read(min(65536, limit + 1)):
                size += len(chunk)
                if size > limit:
                    raise ValueError("native evidence Docker response exceeds its byte budget")
                chunks.append(chunk)
            if await process.wait() != 0:
                raise ConnectionError("native evidence Docker read failed")
            return b"".join(chunks)
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


def completion_records(body: bytes, service: str, lower: datetime, upper: datetime,
                       received: datetime) -> tuple[list[dict], dict]:
    """Admit the known completion projection, preserving its original values.

    One malformed business completion quarantines this service snapshot. Keeping
    only its valid neighbours could otherwise make partial evidence look sound.
    Nonbusiness access/start/fault-control log events are not inputs.
    """
    rows, counts = [], Counter()
    if len(body) > LOG_BYTES:
        return [], {"oversized-log-capture": 1}
    try:
        lines = body.decode("utf-8").splitlines()
    except UnicodeError:
        return [], {"invalid-log-encoding": 1}
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            # Docker/Uvicorn also emits non-JSON access/startup lines.
            counts["non-business-line"] += 1
            continue
        if not isinstance(row, dict) or row.get("event") != "request_completed":
            counts["non-business-line"] += 1
            continue
        if (row.get("method"), row.get("route")) != BUSINESS_ROUTES[service]:
            counts["non-business-route"] += 1
            continue
        try:
            reject_control(row)
            if (set(row) - LOG_FIELDS or row.get("service") != service
                or not (LOG_FIELDS - {"path"}) <= row.keys()
                or type(row["timestamp_unix_ms"]) is not int
                or type(row["status_code"]) is not int or not 100 <= row["status_code"] <= 599
                or type(row["duration_ms"]) not in (int, float)
                or not math.isfinite(row["duration_ms"]) or row["duration_ms"] < 0
                or any(not isinstance(row[k], str) or not 1 <= len(row[k]) <= 256
                       for k in ("request_instance_id", "correlation_id"))
                or any(not isinstance(row[k], str) or not re.fullmatch(pattern, row[k]) or int(row[k], 16) == 0
                       for k, pattern in (("trace_id", r"[0-9a-f]{32}"), ("span_id", r"[0-9a-f]{16}")))):
                raise ValueError("invalid business completion")
            instant = row["timestamp_unix_ms"] * 1_000_000
            if not 0 <= row["timestamp_unix_ms"] <= 253402300799999 or instant > epoch_ns(received):
                raise ValueError("business completion is ahead of its receipt clock")
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return [], {"quarantined-service-log-capture": 1,
                        "body_sha256": hashlib.sha256(body).hexdigest()}
        if epoch_ns(lower) <= instant <= epoch_ns(upper):
            # Use the instrumented route template. Concrete URL paths may contain
            # identifiers and are not needed by log/trace RCA support.
            rows.append({key: value for key, value in row.items() if key != "path"})
            if len(rows) > LOG_RECORDS:
                return [], {"too-many-business-completions": 1}
    return rows, dict(counts)


def select_traces(rows: list[dict], latency_ms: float) -> list[tuple[str, str]]:
    """Choose traces by observed completion violations, with equal entry budgets."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["trace_id"]].append(row)
    candidates = defaultdict(list)
    for trace_id, records in grouped.items():
        if not any(r["status_code"] >= 500 or r["duration_ms"] > latency_ms for r in records):
            continue
        services = {r["service"] for r in records}
        entry = "checkout-service" if "checkout-service" in services else next(iter(services)) if len(services) == 1 else None
        if entry is not None:
            candidates[entry].append((max(r["timestamp_unix_ms"] for r in records), trace_id))
    return [(trace_id, entry) for entry in sorted(candidates)
            for _, trace_id in sorted(candidates[entry], reverse=True)[:TRACES_PER_ENTRY]]


class NativeIncidentEvidence:
    def __init__(self, project: str, *, jaeger_endpoint: str = "http://127.0.0.1:16686",
                 prometheus_endpoint: str = "http://127.0.0.1:9090", commerce_dsn: str | None = None,
                 transport=None):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
            raise ValueError("native evidence requires an explicit local Compose project")
        self.project = project
        self.jaeger_endpoint, self.prometheus_endpoint = local_url(jaeger_endpoint), local_url(prometheus_endpoint)
        self.infrastructure = None
        if commerce_dsn is not None:
            parsed = urlsplit(commerce_dsn)
            if (parsed.scheme not in ("postgres", "postgresql") or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or not parsed.port or parsed.path != "/sentinelops" or parsed.username != "sentinel"
                or parsed.query or parsed.fragment):
                raise ValueError("native infrastructure evidence requires the explicit loopback commerce database")
            self.infrastructure = PostgresInfrastructureSource(commerce_dsn)
            self.infrastructure_port = parsed.port
        self.client = httpx.AsyncClient(timeout=5., trust_env=False, follow_redirects=False, transport=transport)
        self.profile = {"profile_version": "native-incident-evidence/v1", "compose_project": project,
            "jaeger_endpoint": self.jaeger_endpoint, "prometheus_endpoint": self.prometheus_endpoint,
            "infrastructure_source": self.infrastructure.source_ref if self.infrastructure else None,
            "log_byte_limit_per_service": LOG_BYTES, "completion_limit_per_service": LOG_RECORDS,
            "trace_byte_limit": TRACE_BYTES, "traces_per_entry": TRACES_PER_ENTRY,
            "docker_timeout_seconds": 8, "trace_timeout_seconds": 6}

    async def close(self):
        await self.client.aclose()

    async def _owned(self, service: str, private_port: str | None = None, host_port: int | None = None) -> dict:
        identifiers = (await docker_read("ps", "--all", "--filter", "label=com.docker.compose.project=" + self.project,
            "--filter", "label=com.docker.compose.service=" + service, "--format", "{{.ID}}", limit=4096)).decode().split()
        if len(identifiers) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", identifiers[0]):
            raise ValueError("native evidence source must resolve to exactly one owned container")
        # Only allowlisted inspect fields; do not read Config.Env or process args.
        raw = await docker_read("inspect", "--format",
            '{"id":{{json .Id}},"labels":{{json .Config.Labels}},"ports":{{json .NetworkSettings.Ports}},"bindings":{{json .HostConfig.PortBindings}},"running":{{json .State.Running}}}',
            identifiers[0], limit=32768)
        selected = json.loads(raw)
        labels = selected["labels"]
        if (labels.get("com.docker.compose.project") != self.project
            or labels.get("com.docker.compose.service") != service):
            raise ValueError("native evidence ownership changed during inspection")
        if private_port is not None:
            published = (selected["ports"] if selected["running"] else selected["bindings"]).get(private_port)
            if not published or any(p["HostIp"] not in ("127.0.0.1", "::1") or p["HostPort"] != str(host_port) for p in published):
                raise ValueError("native evidence endpoint is not the selected owned loopback port")
        return {"service": service, "container_id": selected["id"], "compose_project": self.project,
                "running": selected["running"]}

    async def _http(self, path: str, *, limit: int) -> bytes:
        async with asyncio.timeout(6.):
            async with self.client.stream("GET", path) as response:
                response.raise_for_status()
                pieces, size = [], 0
                async for piece in response.aiter_bytes():
                    size += len(piece)
                    if size > limit:
                        raise ValueError("native evidence HTTP response exceeds its byte budget")
                    pieces.append(piece)
                return b"".join(pieces)

    async def verify_source(self, identity: dict) -> dict:
        if (identity["environment"] != "local"
            or identity["endpoint_hash"] != stable_id({"endpoint": self.prometheus_endpoint})):
            raise ValueError("operational evidence and accepted health select different source endpoints/environments")
        selected = await asyncio.gather(self._owned("prometheus", "9090/tcp", urlsplit(self.prometheus_endpoint).port),
            self._owned("jaeger", "16686/tcp", urlsplit(self.jaeger_endpoint).port),
            *(self._owned(service) for service in sorted(BUSINESS_ROUTES)))
        if self.infrastructure:
            selected.append(await self._owned("postgres", "5432/tcp", self.infrastructure_port))
        owned = {item["service"]: item for item in selected}
        if owned["prometheus"]["running"]:
            try:
                config = json.loads(await self._http(self.prometheus_endpoint + "/api/v1/status/config", limit=TRACE_BYTES))
            except (httpx.HTTPError, TimeoutError):
                owned["prometheus"]["configuration_available"] = False
            else:
                if (config.get("status") != "success"
                    or stable_id(yaml.safe_load(config["data"]["yaml"])) != identity["prometheus_configuration_hash"]):
                    raise ValueError("native source configuration changed since its accepted health identity")
                owned["prometheus"]["configuration_available"] = True
        return owned

    async def _logs(self, identity: dict, lower: datetime, upper: datetime) -> tuple[list[dict], dict]:
        try:
            body = await docker_read("logs", "--since", timestamp(lower), "--until", timestamp(upper), identity["container_id"])
            received = now()
            records, counts = completion_records(body, identity["service"], lower, upper, received)
            return records, {**identity, "collected_at": timestamp(received), "selection": counts,
                             "response_sha256": hashlib.sha256(body).hexdigest()}
        except (OSError, ValueError, TimeoutError):
            return [], {**identity, "collected_at": timestamp(now()), "unavailable": "bounded-docker-log-read-failed"}

    async def _trace(self, trace_id: str, entry: str, store) -> tuple[str | None, str | None]:
        try:
            body = (await self._http(self.jaeger_endpoint + "/api/v3/traces/" + trace_id, limit=TRACE_BYTES)).decode("utf-8")
            reject_control([json.loads(line) for line in body.splitlines() if line.strip()])
            decode_v3_trace(body, trace_id)
            raw = {"kind": "trace", "trace_id": trace_id, "request_entry_service": entry,
                   "body": body, "collected_at": timestamp(now())}
            return store.write("raw", raw), None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, UnicodeError, TimeoutError, RecursionError):
            return None, "unavailable-or-unsupported-bounded-trace"

    async def capture(self, store, *, job_id: str, intent_ref: str, group: dict, policy: dict,
                      health_identity: dict, latency_ms: float) -> str:
        validate_policy(policy)
        owned = await self.verify_source(health_identity)
        lower = utc_time(group["first_onset_at"])
        upper = min(now(), lower + timedelta(seconds=policy["evidence_window_seconds"]))
        if lower > upper:
            raise ValueError("cannot collect operational evidence before its source onset")
        parts = await asyncio.gather(*(self._logs(owned[s], lower, upper) for s in sorted(BUSINESS_ROUTES)))
        records = [row for rows, _ in parts for row in rows]
        receipts = [receipt for _, receipt in parts]
        logs = {"kind": "serving-logs", "projection_version": "business-completion/v1", "records": records,
                "collected_at": timestamp(now()), "source_receipts": receipts}
        logs_ref = store.write("raw", logs)
        # At most three HTTP reads at once; all selected IDs come from the captured business logs.
        semaphore = asyncio.Semaphore(3)
        async def trace(selected):
            async with semaphore:
                return await self._trace(*selected, store) if owned["jaeger"]["running"] else (None, "jaeger-unavailable")
        traces = await asyncio.gather(*(trace(selected) for selected in select_traces(records, latency_ms)))
        infra_refs, missing = [], Counter(error for _, error in traces if error)
        if self.infrastructure and owned["postgres"]["running"]:
            try:
                observation = await self.infrastructure.capture(environment="local")
                infra_refs.append(store.write("observations", observation))
            except (OSError, ValueError, TimeoutError, asyncpg.PostgresError):
                missing["infrastructure-unavailable"] += 1
        else:
            missing["infrastructure-unavailable" if self.infrastructure else "infrastructure-not-configured"] += 1
        capture = {"capture_version": "continuous-incident-evidence/v1", "job_id": job_id,
            "intent_ref": intent_ref, "profile": self.profile, "health_stream_id": stable_id(health_identity),
            "window_start": timestamp(lower), "window_end": timestamp(upper),
            "analyzed_at": timestamp(now()), "evidence_refs": {"logs": logs_ref,
                "traces": sorted(ref for ref, _ in traces if ref), "infrastructure": infra_refs},
            "source_containers": owned, "missing_counts": dict(missing), "actions_allowed": False}
        return store.write("manifests", capture)
