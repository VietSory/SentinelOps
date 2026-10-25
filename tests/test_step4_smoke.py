import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from scripts import step4_smoke_test as smoke
from sentinelops.telemetry.store import ArtifactStore


def test_step4_restores_both_faults_when_initial_configuration_fails(monkeypatch, tmp_path):
    original = {
        8002: {"mode": "error", "delay_ms": 0},
        8003: {"mode": "latency", "delay_ms": 100},
    }
    faults = deepcopy(original)
    failed = False

    def handle(request):
        nonlocal failed
        if request.url.path == "/internal/faults":
            if request.method == "POST":
                payload = json.loads(request.content)
                if request.url.port == 8002 and payload["mode"] == "healthy" and not failed:
                    failed = True
                    return httpx.Response(503)
                faults[request.url.port] = payload
            return httpx.Response(200, json=faults[request.url.port])
        return httpx.Response(200)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(smoke.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(smoke.exercise(ArtifactStore(tmp_path), 3.))
    assert faults == original
