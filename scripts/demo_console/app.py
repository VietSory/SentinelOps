"""Run with python -m scripts.demo_console. Never creates a port-forward."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
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
)
ACTION_BY_ID = {action.id: action for action in ACTIONS}


class Snapshot(BaseModel):
    service: str
    mode: FaultMode
    delay_ms: int = Field(strict=True, ge=0, le=10_000)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeploymentRunner:
    """One inherited-environment Bash process; logs cannot fill a pipe or memory."""

    def __init__(self, script: str):
        self.script = script
        self.process: subprocess.Popen | None = None
        self.output = None

    def configuration_error(self):
        if not self.script:
            return "Deployment script is not configured. DEMO_CONSOLE_DEPLOY_SCRIPT requires an absolute path to B7 or B9."
        path = Path(self.script)
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.R_OK):
            return "DEMO_CONSOLE_DEPLOY_SCRIPT must point to an existing, readable file using an absolute path."
        return None

    def snapshot(self):
        code = self.process.poll() if self.process else None
        state = "idle" if self.process is None else "running" if code is None else "exited"
        tail = ""
        if self.output:
            size = os.fstat(self.output.fileno()).st_size
            # pread does not move the shared child file offset while reading its log.
            tail = os.pread(self.output.fileno(), 8192, max(0, size - 8192)).decode("utf-8", errors="replace")
        error = self.configuration_error()
        return {"configured": error is None, "configuration_error": error,
                "state": state, "returncode": code, "log_tail": tail}

    def start(self):
        error = self.configuration_error()
        if error:
            raise HTTPException(503, error)
        if self.process and self.process.poll() is None:
            raise HTTPException(409, "The deployment script is already running.")
        if self.output:
            self.output.close()
        self.output = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(
                ["bash", self.script], cwd=ROOT, env=os.environ.copy(),
                stdin=subprocess.DEVNULL, stdout=self.output, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            self.output.close()
            self.output = None
            self.process = None
            raise HTTPException(503, "Could not start the deployment script. Check Bash, the script path and read permissions.") from exc


async def observe(client: httpx.AsyncClient, settings: Settings, service: str):
    endpoint = settings.endpoint(service)
    try:
        response = await client.get(endpoint)
        response.raise_for_status()
        snapshot = Snapshot.model_validate(response.json())
        if snapshot.service != SERVICE_NAMES[service]:
            raise ValueError("wrong service")
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
    return {"ok": False, "mode": None, "delay_ms": None, "observed_at": None,
            "error": error}


def create_app(settings: Settings | None = None, *, transport=None, runner=None):
    settings = settings or Settings.from_env()
    runner = runner or DeploymentRunner(settings.deploy_script)
    action_lock = asyncio.Lock()

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

    async def observations():
        rows = await asyncio.gather(*(observe(app.state.upstream, settings, s) for s in SERVICE_NAMES))
        return dict(zip(SERVICE_NAMES, rows))

    @app.get("/api/status")
    async def status():
        return {"enabled": settings.enabled, "actions": [a.public() for a in ACTIONS],
                "services": await observations(), "deployment": runner.snapshot(),
                "busy": action_lock.locked(), "checked_at": now()}

    @app.post("/api/actions/{action_id}")
    async def execute(action_id: str):
        if not settings.enabled:
            raise HTTPException(403, "Fault injection is off. Actions require DEMO_CONSOLE_ENABLE_FAULTS=true at startup.")
        action = ACTION_BY_ID.get(action_id)
        if action is None:
            raise HTTPException(404, "Fault scenario not found.")
        if action_lock.locked() or runner.snapshot()["state"] == "running":
            raise HTTPException(409, "An action is in progress. If the deployment script is running, wait for it to finish.")
        async with action_lock:
            if action_id == "deploy-bad-image":
                runner.start()
                return JSONResponse({"accepted": True, "message": "The script started. Deployment and recovery are unconfirmed.",
                                     "deployment": runner.snapshot()}, status_code=202)
            results = {}
            for service in action.services:
                # Check service identity before sending any mutation to a configurable URL.
                before = await observe(app.state.upstream, settings, service)
                if not before["ok"]:
                    results[service] = {"acknowledged": False, "error": before["error"]}
                    continue
                try:
                    response = await app.state.upstream.post(settings.endpoint(service), json=action.payload())
                    response.raise_for_status()
                    results[service] = {"acknowledged": True, "error": None}
                except httpx.HTTPStatusError as exc:
                    results[service] = {"acknowledged": False, "error": f"POST {service}: HTTP {exc.response.status_code}. Refresh the current fault mode."}
                except httpx.RequestError:
                    results[service] = {"acknowledged": False, "error": f"POST to {service} lost its connection. The action may have taken effect. Check the port-forward and current fault mode before retrying."}
            # POST acknowledgement is never used as a current-state observation.
            services = await observations()
            verified = all(results[s]["acknowledged"] and services[s]["ok"]
                           and all(services[s][key] == value for key, value in action.payload().items())
                           for s in action.services)
            return JSONResponse({"verified": verified, "results": results, "services": services,
                                 "message": "The latest read confirms the requested fault mode." if verified else "The action may have applied only in part. Check each service before retrying."},
                                status_code=200 if verified else 502)

    return app


def main():
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port,
                proxy_headers=False, workers=1)
