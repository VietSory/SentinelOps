"""Bounded local operational evidence; never imports experiment controls/truth."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

import httpx

from sentinelops.incidents.rca_source import reject_control
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp
from sentinelops.workers.health import local_endpoint


class NativeEvidenceSource:
    def __init__(self, project: str, *, directory: Path, jaeger="http://127.0.0.1:16686"):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
            raise ValueError("explicit local Compose project required")
        self.project, self.directory, self.jaeger = project, directory.resolve(), local_endpoint(jaeger)
        self.profile = "native-completion-traces/v1:" + project

    def capture(self, store, closed):
        since = closed["scope"]["first_onset_at"]
        # Docker has no response limit: spool to a file and enforce a byte budget
        # before parsing. --tail bounds records per container as well.
        import tempfile
        with tempfile.TemporaryFile() as output:
            subprocess.run(["docker", "compose", "-p", self.project, "logs", "--no-color", "--no-log-prefix",
                            "--since", since, "--tail", "20000", *sorted(BUSINESS_ROUTES)], cwd=self.directory,
                           stdout=output, stderr=subprocess.DEVNULL, timeout=10, check=True)
            if output.tell() > 32_000_000:
                raise ValueError("completion log snapshot exceeds byte budget")
            output.seek(0)
            records = []
            for line in output:
                if not line.lstrip().startswith(b"{"):
                    continue
                item = json.loads(line)
                service = item.get("service")
                if (item.get("event") != "request_completed" or service not in BUSINESS_ROUTES
                    or (item.get("method"), item.get("route")) != BUSINESS_ROUTES[service]):
                    continue
                # Reject truth enrichment before taking only required completion fields.
                reject_control(item)
                fields = ("event", "service", "method", "route", "timestamp_unix_ms", "duration_ms", "status_code",
                          "request_instance_id", "trace_id", "span_id", "correlation_id")
                records.append({key: item[key] for key in fields if key in item})
        logs = store.write("raw", {"kind": "serving-logs", "records": records,
                                   "collected_at": timestamp(datetime.now(timezone.utc))})
        # Include each entry scope; direct failures must not masquerade as a cascade.
        groups = {}
        for item in records:
            trace_id = item.get("trace_id")
            if isinstance(trace_id, str) and re.fullmatch(r"[0-9a-f]{32}", trace_id):
                groups.setdefault(trace_id, set()).add(item["service"])
        traces, unavailable = [], []
        with httpx.Client(timeout=1., trust_env=False) as client:
            for trace_id in sorted(groups)[:24]:
                entry = "checkout-service" if "checkout-service" in groups[trace_id] else sorted(groups[trace_id])[0]
                try:
                    response = client.get(self.jaeger + "/api/v3/traces/" + trace_id)
                    response.raise_for_status()
                    if len(response.content) > 2_000_000:
                        raise ValueError("trace response exceeds byte budget")
                    traces.append(store.write("raw", {"kind": "trace", "trace_id": trace_id,
                        "request_entry_service": entry, "body": response.text,
                        "collected_at": timestamp(datetime.now(timezone.utc))}))
                except httpx.HTTPError as exc:
                    unavailable.append(type(exc).__name__)
        # Missing traces stay missing; collection failures are inspectable operational data.
        store.write("manifests", {"evidence_profile": self.profile, "logs_ref": logs,
                    "trace_refs": traces, "unavailable_trace_errors": unavailable, "infrastructure_available": False})
        return {"logs": logs, "traces": traces, "infrastructure": []}
