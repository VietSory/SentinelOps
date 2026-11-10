"""Bounded lock injection for the dedicated local S4 Compose testbed."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess

import asyncpg

DATABASE_URL = "postgresql://sentinel:sentinel@127.0.0.1:5432/sentinelops"
PULSE_GAP_SECONDS = .4
PORTS = {
    "postgres": ("5432/tcp", "5432"), "checkout-service": ("8000/tcp", "8001"),
    "payment-service": ("8000/tcp", "8002"), "inventory-service": ("8000/tcp", "8003"),
    "prometheus": ("9090/tcp", "9090"), "jaeger": ("16686/tcp", "16686"),
}


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
    identifiers = subprocess.check_output(
        ["docker", "compose", "ps", "-q", *PORTS], cwd=project, text=True, timeout=10,
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
        private, published = PORTS[service]
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


async def lock_pulses(hold_seconds: float, project: Path, expected_project: str):
    if await asyncio.to_thread(require_sandbox, project) != expected_project:
        raise ValueError("S4 Compose project changed before lock injection")
    connection = await asyncpg.connect(DATABASE_URL, timeout=5, server_settings={
        "lock_timeout": "250ms", "idle_in_transaction_session_timeout": "2500ms",
        "statement_timeout": "2500ms",
    })
    try:
        while True:
            async with connection.transaction():
                await connection.execute("LOCK TABLE public.payments IN SHARE MODE")
                await asyncio.sleep(hold_seconds)
            await asyncio.sleep(PULSE_GAP_SECONDS)
    finally:
        await connection.close(timeout=5)
