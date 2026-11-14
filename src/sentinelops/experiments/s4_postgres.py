"""Bounded lock injection for the dedicated local S4 Compose testbed."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

import asyncpg

from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import timestamp
from sentinelops.telemetry.infrastructure import PostgresInfrastructureSource

DATABASE_URL = "postgresql://sentinel:sentinel@127.0.0.1:5432/sentinelops"
PULSE_GAP_SECONDS = .4
LOCK_LOG = re.compile(
    r"(?P<logged_at>[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}) UTC "
    r"\[(?P<pid>[0-9]+)\] LOG:\s+process (?P=pid) (?P<event>still waiting for|acquired) "
    r"RowExclusiveLock on relation (?P<relation>[0-9]+) of database (?P<database>[0-9]+) "
    r"after (?P<ms>[0-9]+)\.(?P<fraction>[0-9]{3}) ms(?: at character [0-9]+)?"
)
PORTS = {
    "postgres": ("5432/tcp", "5432"), "checkout-service": ("8000/tcp", "8001"),
    "payment-service": ("8000/tcp", "8002"), "inventory-service": ("8000/tcp", "8003"),
    "prometheus": ("9090/tcp", "9090"), "jaeger": ("16686/tcp", "16686"),
}


def database_port() -> int:
    """Only the local published port is configurable; host, role and database stay pinned."""
    value = os.getenv("SENTINELOPS_S4_POSTGRES_PORT", "5432")
    if not re.fullmatch(r"[1-9][0-9]{0,4}", value) or int(value) > 65535:
        raise ValueError("S4 PostgreSQL port must be an integer from 1 to 65535")
    return int(value)


def database_url() -> str:
    return f"postgresql://sentinel:sentinel@127.0.0.1:{database_port()}/sentinelops"


def require_sandbox(project: Path) -> str:
    name = os.getenv("COMPOSE_PROJECT_NAME", "")
    if not re.fullmatch(r"sentinelops-s4-[a-z0-9][a-z0-9-]{0,39}", name):
        raise ValueError("S4 requires an explicit, dedicated sentinelops-s4-* Compose project")
    if os.getenv("DOCKER_HOST") or os.getenv("DOCKER_CONTEXT"):
        raise ValueError("S4 requires the default local Docker endpoint")
    endpoint = subprocess.check_output(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        cwd=project, text=True, timeout=10,
    ).strip()
    if endpoint != "unix:///var/run/docker.sock":
        raise ValueError("S4 requires the default local Docker endpoint")
    ports = {**PORTS, "postgres": ("5432/tcp", str(database_port()))}
    identifiers = subprocess.check_output(
        ["docker", "compose", "ps", "-q", *ports], cwd=project, text=True, timeout=10,
    ).split()
    if len(identifiers) != len(PORTS):
        raise ValueError("S4 requires every dedicated source container to be running")
    containers = json.loads(subprocess.check_output(["docker", "inspect", *identifiers], text=True, timeout=10))
    verified = set()
    for item in containers:
        labels = item["Config"]["Labels"]
        service = labels.get("com.docker.compose.service")
        if labels.get("com.docker.compose.project") != name or service not in PORTS or not item["State"]["Running"]:
            raise ValueError("S4 source container does not belong to the dedicated project")
        private, published = ports[service]
        if item["NetworkSettings"]["Ports"].get(private) != [{"HostIp": "127.0.0.1", "HostPort": published}]:
            raise ValueError(f"S4 {service} is not bound to the expected local experiment port")
        if service == "postgres":
            database_mounts = [mount for mount in item["Mounts"] if mount["Destination"] == "/var/lib/postgresql/data"]
            if len(database_mounts) != 1 or (database_mounts[0].get("Type"), database_mounts[0].get("Name")) != (
                    "volume", name + "_sentinelops_postgres"):
                raise ValueError("S4 PostgreSQL must use its dedicated Compose data volume")
        verified.add(service)
    if verified != set(PORTS):
        raise ValueError("S4 dedicated sources are incomplete")
    return name


def pressure_stages(active_seconds: int, hold_ms: int, lead_in: bool):
    main = (("fault", active_seconds, hold_ms),)
    if lead_in:
        return (("degrading", 180, 350), *main)
    return main


async def lock_pulses(hold_seconds: float, project: Path, expected_project: str, *, stats=None):
    if await asyncio.to_thread(require_sandbox, project) != expected_project:
        raise ValueError("S4 Compose project changed before lock injection")
    connection = await asyncpg.connect(database_url(), timeout=5, server_settings={
        "lock_timeout": "250ms", "idle_in_transaction_session_timeout": "2500ms",
        "statement_timeout": "2500ms",
    })
    counters = stats if stats is not None else {}
    for key in ("attempts", "acquired", "lock_timeouts"):
        counters.setdefault(key, 0)
    try:
        while True:
            counters["attempts"] += 1
            try:
                async with connection.transaction():
                    await connection.execute("LOCK TABLE public.payments IN SHARE MODE")
                    counters["acquired"] += 1
                    await asyncio.sleep(hold_seconds)
            except asyncpg.LockNotAvailableError:
                # A bounded acquisition timeout is expected under concurrent writes.
                # The transaction has rolled back before the next paced attempt.
                counters["lock_timeouts"] += 1
            await asyncio.sleep(PULSE_GAP_SECONDS)
    finally:
        await connection.close(timeout=5)


def project_lock_logs(output: str, *, database: int, relation: int, since: str, ended: str) -> list[dict]:
    lower, upper = utc_time(since), utc_time(ended)
    records = []
    for line in output.splitlines():
        match = LOCK_LOG.fullmatch(line)
        if not match or int(match["database"]) != database or int(match["relation"]) != relation:
            continue
        observed = datetime.fromisoformat(match["logged_at"]).replace(tzinfo=timezone.utc)
        if lower <= observed <= upper:
            records.append({
                "observed_at": timestamp(observed),
                "event": "waiting" if match["event"] == "still waiting for" else "acquired",
                "duration_us": int(match["ms"]) * 1000 + int(match["fraction"]),
            })
    return records


async def capture_lock_logs(project: Path, expected_project: str, since: str, ended: str) -> dict | None:
    if await asyncio.to_thread(require_sandbox, project) != expected_project:
        raise ValueError("S4 Compose project changed before lock-log capture")
    connection = await asyncpg.connect(database_url(), timeout=5, server_settings={"default_transaction_read_only": "on"})
    try:
        native = await connection.fetchrow("""
            SELECT current_setting('log_lock_waits') AS enabled,
                   pg_postmaster_start_time() AS postgres_started_at,
                   (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database()) AS database_oid,
                   'public.payments'::regclass::oid AS relation_oid
        """, timeout=5)
    finally:
        await connection.close(timeout=5)
    if native["enabled"] != "on":
        return None
    return await asyncio.to_thread(_read_lock_logs, project, expected_project, native, since, ended)


def _read_lock_logs(project, expected_project, native, since, ended):
    identifier = subprocess.check_output(
        ["docker", "compose", "ps", "-q", "postgres"], cwd=project, text=True, timeout=10,
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise ValueError("S4 lock logs require one identified PostgreSQL container")
    ownership = subprocess.check_output(
        ["docker", "inspect", "--format", '{{index .Config.Labels "com.docker.compose.project"}} '
         '{{index .Config.Labels "com.docker.compose.service"}}', identifier], text=True, timeout=10,
    ).strip()
    if ownership != f"{expected_project} postgres":
        raise ValueError("S4 lock logs are outside the dedicated PostgreSQL project")
    output = subprocess.check_output(
        ["docker", "logs", "--since", since, "--until", ended, "--tail", "20000", identifier],
        stderr=subprocess.STDOUT, text=True, timeout=30,
    )
    if len(output.splitlines()) >= 20000:
        raise ValueError("S4 PostgreSQL log read reached its bounded tail")
    receipt = timestamp(datetime.now(timezone.utc))
    return {
        "kind": "postgres-lock-wait-log/v1",
        "source_ref": PostgresInfrastructureSource(database_url()).source_ref,
        "postgres_started_at": timestamp(native["postgres_started_at"]),
        "database_oid": native["database_oid"],
        "relation_oid": native["relation_oid"],
        "collected_at": receipt,
        "records": project_lock_logs(output, database=native["database_oid"], relation=native["relation_oid"],
                                     since=since, ended=ended),
    }
