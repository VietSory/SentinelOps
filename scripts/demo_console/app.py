"""Run with python -m scripts.demo_console. Never creates a port-forward."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError

from services.common.faults import FaultMode, FaultRequest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SERVICE_NAMES = {"payment": "payment-service", "inventory": "inventory-service"}


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    payment_url: str = "http://127.0.0.1:18081"
    inventory_url: str = "http://127.0.0.1:18082"
    port: int = 8765
    deploy_script: str = ""
    oom_script: str = ""
    oom_enabled: bool = False

    def __post_init__(self):
        if not 1 <= self.port <= 65535:
            raise ValueError("DEMO_CONSOLE_PORT must be between 1 and 65535.")
        for service in SERVICE_NAMES:
            value = getattr(self, service + "_url")
            parsed = urlsplit(value)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.path not in {"", "/"}):
                raise ValueError(f"DEMO_CONSOLE_{service.upper()}_URL must be an HTTP(S) origin without credentials, a path or a query.")
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError(f"Invalid port for {service}.") from exc

    @classmethod
    def from_env(cls):
        return cls(
            enabled=os.getenv("DEMO_CONSOLE_ENABLE_FAULTS", "false").lower() == "true",
            payment_url=os.getenv("DEMO_CONSOLE_PAYMENT_URL", "http://127.0.0.1:18081"),
            inventory_url=os.getenv("DEMO_CONSOLE_INVENTORY_URL", "http://127.0.0.1:18082"),
            port=int(os.getenv("DEMO_CONSOLE_PORT", "8765")),
            deploy_script=os.getenv("DEMO_CONSOLE_DEPLOY_SCRIPT", ""),
            oom_script=os.getenv("DEMO_CONSOLE_OOM_SCRIPT", ""),
            oom_enabled=os.getenv("DEMO_CONSOLE_ENABLE_OOM", "false").lower() == "true",
        )

    def endpoint(self, service: str) -> str:
        return getattr(self, service + "_url").rstrip("/") + "/internal/faults"

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@dataclass(frozen=True)
class Action:
    id: str
    title: str
    description: str
    services: tuple[str, ...]
    mode: FaultMode | None = None
    delay_ms: int = 0

    def payload(self):
        return FaultRequest(mode=self.mode, delay_ms=self.delay_ms).model_dump()

    def public(self):
        return {"id": self.id, "title": self.title, "description": self.description,
                "services": self.services,
                "fault": self.payload() if self.mode else None}


ACTIONS = (
    Action("payment-latency-950", "Payment · 950 ms delay", "Add 950 ms to each payment request.", ("payment",), "latency", 950),
    Action("payment-latency-2000", "Payment · 2,000 ms delay", "Add 2 seconds to each payment request.", ("payment",), "latency", 2000),
    Action("payment-error", "Payment · HTTP 503", "Make the Payment API return HTTP 503.", ("payment",), "error"),
    Action("inventory-latency-950", "Inventory · 950 ms delay", "Add 950 ms to each inventory request.", ("inventory",), "latency", 950),
    Action("inventory-error", "Inventory · HTTP 503", "Make the Inventory API return HTTP 503.", ("inventory",), "error"),
    Action("both-error", "Both services · HTTP 503", "Make Payment and Inventory return HTTP 503. Each service reports its result separately.", ("payment", "inventory"), "error"),
    Action("recover", "Reset fault modes", "Set both fault modes to healthy with zero delay. Verify the deployed image and service health separately.", ("payment", "inventory"), "healthy"),
    Action("deploy-bad-image", "Deploy regression image", "Start the configured B7 or B9 script. Check rollout and recovery evidence separately from the fault mode.", ("payment",)),
    Action("oom", "Pod memory · OOM", "Run the configured bounded OOM scenario on the isolated demo workload. Recovery still requires approval in the incident workspace.", ()),
)
ACTION_BY_ID = {action.id: action for action in ACTIONS}


class Snapshot(BaseModel):
    service: str
    mode: FaultMode
    delay_ms: int = Field(strict=True, ge=0, le=10_000)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_output(value: str, *, limit: int = 8192) -> str:
    """A terminal preview must not turn script output into a credential export."""
    value = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", value)
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = "".join(c for c in value if c in "\n\t" or ord(c) >= 32 and ord(c) != 127)
    # Also remove credentials inherited by reviewed scripts even if a script
    # prints a value without its field name. Never return environment metadata.
    for key, secret in os.environ.items():
        if secret and len(secret) >= 6 and re.search(r"TOKEN|SECRET|PASSWORD|CREDENTIAL|DATABASE_URL|DSN|(?:API|ACCESS|PRIVATE)_?KEY", key, re.I):
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)",
                   "[redacted private key]", value, flags=re.S)
    key_name = r"[-\w]*(?:token|password|secret|credential|dsn|api[-_]?key|access[-_]?key|private[-_]?key)[-\w]*"
    # Decode one JSON value at a time, rather than using a regex to guess where
    # nested arrays/objects or escaped quoted strings end. Script output can
    # contain JSON amid other log lines, so a whole-log JSON parse is insufficient.
    decoder = json.JSONDecoder()
    spans = []
    covered = 0
    for match in re.finditer(r'"(?:authorization|' + key_name + r')"\s*:\s*', value, re.I):
        if match.start() < covered:
            continue
        try:
            _decoded, end = decoder.raw_decode(value, match.end())
        except ValueError:
            continue
        spans.append((match.end(), end))
        covered = end
    for start, end in reversed(spans):
        value = value[:start] + '"[redacted]"' + value[end:]
    sensitive = re.compile(
        r"authorization|(?:^|[\s\"'])" + key_name + r"[\"']?\s*(?:[:=]|\s+\S)|"
        r"(?:postgres(?:ql)?|mysql)://|https?://[^\s/]+@|[?&](?:x-amz-|signature=)|"
        r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", re.I)
    empty_value = re.compile(r"(?:authorization|" + key_name + r")[\"']?\s*[:=]\s*(?:Bearer|Basic)?\s*$", re.I)
    lines = []
    continuation = False
    for line in value.splitlines(keepends=True):
        if sensitive.search(line):
            lines.append("[redacted sensitive log line]\n")
            # JSON formatting and HTTP header continuation may put the value
            # on the following line. Do not leave that orphan value visible.
            continuation = bool(empty_value.search(line.rstrip()))
        elif continuation:
            lines.append("[redacted sensitive log continuation]\n")
            continuation = not bool(line.strip())
        else:
            lines.append(line)
    value = "".join(lines)
    encoded = value.encode("utf-8")
    if len(encoded) > limit:
        value = encoded[-limit:].decode("utf-8", errors="ignore")
        value = value.split("\n", 1)[1] if "\n" in value else "[log line exceeds preview bound]"
    return value


class ExecutionJournal:
    """Bounded command/result history; periodic status reads do not append commands."""

    def __init__(self):
        self.entries = deque(maxlen=200)
        self.sequence = 0
        self.lock = threading.Lock()

    def add(self, action_id: str, command: str, state: str, detail: str = ""):
        with self.lock:
            self.sequence += 1
            self.entries.append({"sequence": self.sequence, "at": now(), "action_id": action_id,
                                 "command": safe_output(command, limit=2048), "state": state,
                                 "detail": safe_output(detail, limit=2048)})

    def snapshot(self):
        with self.lock:
            return [dict(entry) for entry in self.entries]


class DeploymentRunner:
    """One inherited-environment Bash process; logs cannot fill a pipe or memory."""

    def __init__(self, script: str, *, environment_key="DEMO_CONSOLE_DEPLOY_SCRIPT"):
        self.script = script
        self.environment_key = environment_key
        self.process: subprocess.Popen | None = None
        self.output = None
        self.journal: ExecutionJournal | None = None
        self.action_id = "deploy-bad-image"
        self.exit_recorded = False

    @property
    def command(self):
        return shlex.join(["bash", self.script])

    def configuration_error(self):
        if not self.script:
            return f"Scenario script is not configured. {self.environment_key} requires an absolute path to a reviewed script."
        path = Path(self.script)
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.R_OK):
            return f"{self.environment_key} must point to an existing, readable file using an absolute path."
        return None

    def snapshot(self):
        code = self.process.poll() if self.process else None
        state = "idle" if self.process is None else "running" if code is None else "exited"
        tail = ""
        if self.output:
            size = os.fstat(self.output.fileno()).st_size
            # pread does not move the shared child file offset while reading its log.
            offset = max(0, size - 16384)
            tail = os.pread(self.output.fileno(), 16384, offset).decode("utf-8", errors="replace")
            # A truncated leading line might contain a token whose field name
            # was outside the read window. Do not publish that partial line.
            if offset:
                tail = tail.split("\n", 1)[1] if "\n" in tail else "[log line exceeds preview bound]"
            tail = safe_output(tail)
        if code is not None and not self.exit_recorded:
            self.exit_recorded = True
            if self.journal:
                self.journal.add(self.action_id, self.command, "exited", f"Process exit code {code}. Service recovery is unconfirmed.")
        error = self.configuration_error()
        return {"configured": error is None, "configuration_error": error,
                "state": state, "returncode": code, "log_tail": tail}

    def start(self):
        error = self.configuration_error()
        if error:
            if self.journal:
                self.journal.add(self.action_id, "", "blocked", error)
            raise HTTPException(503, error)
        if self.process and self.process.poll() is None:
            raise HTTPException(409, "The deployment script is already running.")
        if self.output:
            self.output.close()
        self.output = tempfile.TemporaryFile()
        self.exit_recorded = False
        try:
            if self.journal:
                self.journal.add(self.action_id, self.command, "starting", "Starting the configured scenario script.")
            self.process = subprocess.Popen(
                ["bash", self.script], cwd=ROOT, env=os.environ.copy(),
                stdin=subprocess.DEVNULL, stdout=self.output, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            self.output.close()
            self.output = None
            self.process = None
            if self.journal:
                self.journal.add(self.action_id, self.command, "failed", "Could not start Bash. No scenario process was created.")
            raise HTTPException(503, "Could not start the deployment script. Check Bash, the script path and read permissions.") from exc
        if self.journal:
            self.journal.add(self.action_id, self.command, "running", "Process started. Fault injection and recovery remain unconfirmed.")


async def observe(client: httpx.AsyncClient, settings: Settings, service: str, *, journal=None, action_id=""):
    endpoint = settings.endpoint(service)
    command = "GET " + endpoint
    if journal:
        journal.add(action_id, command, "running", "Reading the actual fault mode.")
    try:
        response = await client.get(endpoint)
        response.raise_for_status()
        snapshot = Snapshot.model_validate(response.json())
        if snapshot.service != SERVICE_NAMES[service]:
            raise ValueError("wrong service")
        if journal:
            journal.add(action_id, command, "succeeded", f"HTTP {response.status_code}: " + json.dumps(snapshot.model_dump(), sort_keys=True))
        return {"ok": True, **snapshot.model_dump(), "observed_at": now(), "error": None}
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        error = f"GET {endpoint}: HTTP {status}."
        if status == 404:
            error += " The fault endpoint may be disabled. Check ENABLE_FAULT_INJECTION=true, DEPLOYMENT_ENVIRONMENT=dev and ALLOW_DEV_FAULT_INJECTION=true on the service."
    except httpx.RequestError:
        error = f"GET {endpoint} could not connect. Check the configured address and port-forward."
    except (ValidationError, ValueError):
        error = f"GET {endpoint}: Invalid fault status or unexpected service name."
    if journal:
        journal.add(action_id, command, "unavailable", error)
    return {"ok": False, "mode": None, "delay_ms": None, "observed_at": None,
            "error": error}


def create_app(settings: Settings | None = None, *, transport=None, runner=None, oom_runner=None):
    settings = settings or Settings.from_env()
    runner = runner or DeploymentRunner(settings.deploy_script)
    oom_runner = oom_runner or DeploymentRunner(settings.oom_script, environment_key="DEMO_CONSOLE_OOM_SCRIPT")
    action_lock = asyncio.Lock()
    journal = ExecutionJournal()
    for action_id, process_runner in (("deploy-bad-image", runner), ("oom", oom_runner)):
        process_runner.journal = journal
        process_runner.action_id = action_id

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(
            timeout=3.0, follow_redirects=False, trust_env=False, transport=transport,
        ) as client:
            app.state.upstream = client
            yield

    app = FastAPI(title="SentinelOps Dev Fault Console", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Fixed Host + peer + Origin keep an unauthenticated local control off
        # remote clients, DNS rebinding and cross-origin browser form submissions.
        if not request.client or request.client.host != "127.0.0.1":
            response = JSONResponse({"detail": "The console accepts clients from 127.0.0.1 only."}, status_code=403)
        elif request.headers.get("host") != f"127.0.0.1:{settings.port}":
            response = JSONResponse({"detail": f"Open the console at {settings.origin}."}, status_code=400)
        elif request.method == "POST" and (
            request.headers.get("origin") != settings.origin
            or request.headers.get("x-console-action") != "apply"
            or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
        ):
            response = JSONResponse({"detail": "Actions must come from the same origin as the console."}, status_code=403)
        else:
            response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        })
        return response

    @app.get("/")
    async def index():
        return FileResponse(HERE / "static" / "index.html")

    @app.get("/assets/{filename}")
    async def asset(filename: str):
        if filename not in {"app.js", "styles.css"}:
            raise HTTPException(404)
        return FileResponse(HERE / "static" / filename)

    async def observations(*, record_action=""):
        rows = await asyncio.gather(*(observe(app.state.upstream, settings, s,
            journal=journal if record_action else None, action_id=record_action) for s in SERVICE_NAMES))
        return dict(zip(SERVICE_NAMES, rows))

    @app.get("/api/status")
    async def status():
        services = await observations()
        deployment, oom = runner.snapshot(), oom_runner.snapshot()
        busy = action_lock.locked() or deployment["state"] == "running" or oom["state"] == "running"
        actions = []
        for action in ACTIONS:
            reason = None
            if not settings.enabled:
                reason = "Fault injection is disabled by configuration."
            elif busy:
                reason = "Another scenario is running."
            elif action.id == "oom" and not settings.oom_enabled:
                reason = "OOM is disabled. Configure the isolated workload and controller, then set DEMO_CONSOLE_ENABLE_OOM=true."
            elif action.id in {"deploy-bad-image", "oom"}:
                reason = (deployment if action.id == "deploy-bad-image" else oom)["configuration_error"]
            elif any(not services[service]["ok"] for service in action.services):
                # A partial reset remains useful when one service is unreachable.
                if action.id != "recover" or not any(services[service]["ok"] for service in action.services):
                    reason = "The target fault endpoint is unavailable. Refresh and check the service connection."
            actions.append({**action.public(), "available": reason is None, "unavailable_reason": reason})
        return {"enabled": settings.enabled, "actions": actions,
                "services": services, "deployment": deployment, "oom": oom,
                "terminal": {"entries": journal.snapshot(), "logs": {
                    "deployment": deployment["log_tail"], "oom": oom["log_tail"]}},
                "busy": busy, "checked_at": now()}

    @app.post("/api/actions/{action_id}")
    async def execute(action_id: str):
        if not settings.enabled:
            raise HTTPException(403, "Fault injection is off. Actions require DEMO_CONSOLE_ENABLE_FAULTS=true at startup.")
        action = ACTION_BY_ID.get(action_id)
        if action is None:
            raise HTTPException(404, "Fault scenario not found.")
        if action_lock.locked() or any(r.snapshot()["state"] == "running" for r in (runner, oom_runner)):
            raise HTTPException(409, "An action is in progress. If the deployment script is running, wait for it to finish.")
        async with action_lock:
            if action_id in {"deploy-bad-image", "oom"}:
                if action_id == "oom" and not settings.oom_enabled:
                    message = "OOM requires DEMO_CONSOLE_ENABLE_OOM=true and a configured scenario script."
                    journal.add(action_id, "", "blocked", message)
                    raise HTTPException(403, message)
                selected = runner if action_id == "deploy-bad-image" else oom_runner
                selected.start()
                key = "deployment" if action_id == "deploy-bad-image" else "oom"
                return JSONResponse({"accepted": True, "message": "The script started. Fault injection and recovery are unconfirmed.",
                                     key: selected.snapshot()}, status_code=202)
            results = {}
            for service in action.services:
                # Check service identity before sending any mutation to a configurable URL.
                before = await observe(app.state.upstream, settings, service, journal=journal, action_id=action_id)
                if not before["ok"]:
                    results[service] = {"acknowledged": False, "error": before["error"]}
                    continue
                try:
                    command = "POST " + settings.endpoint(service) + " " + json.dumps(action.payload(), sort_keys=True)
                    journal.add(action_id, command, "running", "Sending the fixed scenario payload.")
                    response = await app.state.upstream.post(settings.endpoint(service), json=action.payload())
                    response.raise_for_status()
                    journal.add(action_id, command, "acknowledged", f"HTTP {response.status_code}. Reading the fault mode again to verify.")
                    results[service] = {"acknowledged": True, "error": None}
                except httpx.HTTPStatusError as exc:
                    journal.add(action_id, command, "failed", f"HTTP {exc.response.status_code}. No current state is inferred from this response.")
                    results[service] = {"acknowledged": False, "error": f"POST {service}: HTTP {exc.response.status_code}. Refresh the current fault mode."}
                except httpx.RequestError:
                    journal.add(action_id, command, "unknown", "Response lost. The mutation may have applied; inspect the next GET before retrying.")
                    results[service] = {"acknowledged": False, "error": f"POST to {service} lost its connection. The action may have taken effect. Check the port-forward and current fault mode before retrying."}
            # POST acknowledgement is never used as a current-state observation.
            services = await observations(record_action=action_id)
            verified = all(results[s]["acknowledged"] and services[s]["ok"]
                           and all(services[s][key] == value for key, value in action.payload().items())
                           for s in action.services)
            journal.add(action_id, "", "verified" if verified else "unconfirmed",
                        "The observed fault mode matches the requested mode." if verified else
                        "The requested mode was not confirmed for every target. Inspect each service before retrying.")
            return JSONResponse({"verified": verified, "results": results, "services": services,
                                 "message": "The latest read confirms the requested fault mode." if verified else "The action may have applied only in part. Check each service before retrying."},
                                status_code=200 if verified else 502)

    return app


def main():
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port,
                proxy_headers=False, workers=1)
