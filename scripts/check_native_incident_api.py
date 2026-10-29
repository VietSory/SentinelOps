"""Verify protected HTTP history against completed native incident snapshots.

Use CONTROL_DATABASE_URL for the owned local control database. A fresh API token
stays in child environment and memory; selected incident history is read only.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from urllib.parse import urlsplit

import asyncpg
import httpx

from sentinelops.workers.incident_replay import bounded_bytes, json_object, require


async def check(runs: list[Path], output: Path) -> dict:
    require(__debug__, "native API acceptance requires assertions enabled")
    dsn = os.environ.get("CONTROL_DATABASE_URL", "")
    parsed = urlsplit(dsn)
    require(parsed.scheme in ("postgres", "postgresql") and parsed.hostname in ("127.0.0.1", "localhost")
            and parsed.username == "control_writer" and parsed.path == "/sentinelops_control"
            and not parsed.query and not parsed.fragment,
            "native API acceptance requires the selected owned loopback control-writer database")
    output.mkdir(parents=True, exist_ok=False)
    expected, sources = {}, []
    for run in runs:
        blob = bounded_bytes(run / "incident-consumer/database-snapshot.json")
        snapshot, accepted = json_object(blob), json_object(bounded_bytes(run / "incident-consumer/acceptance.json"))
        digest = hashlib.sha256(blob).hexdigest()
        require(accepted["status"] == "PASS" and digest == accepted["database_snapshot_sha256"],
                "selected native incident snapshot differs from its acceptance checksum")
        for job in snapshot["jobs"]:
            require(job["incident_id"] not in expected, "selected API snapshots duplicate incident identity")
            versions = sorted(snapshot[job["job_id"]]["incident_versions"], key=lambda row: row["revision"])
            expected[job["incident_id"]] = [json.loads(v["payload"]) for v in versions]
        sources.append({"consumer_id": snapshot["consumer"]["consumer_id"], "snapshot_sha256": digest})
    require(bool(expected), "native API acceptance requires captured incident jobs")
    connection = await asyncpg.connect(dsn, server_settings={"default_transaction_read_only": "on"})
    server, stream, success = None, None, False

    async def counts():
        versions = await connection.fetchval("SELECT count(*) FROM sentinel_control.incident_versions WHERE incident_id=ANY($1::text[])", list(expected))
        outbox = await connection.fetchval("SELECT count(*) FROM sentinel_control.outbox WHERE body->>'incident_id'=ANY($1::text[])", list(expected))
        return {"versions": versions, "outbox": outbox}

    try:
        before = await counts()
        assert before == {"versions": 4 * len(expected), "outbox": 4 * len(expected)}
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        token = secrets.token_urlsafe(32)
        environment = os.environ.copy()
        environment.pop("DATABASE_URL", None)
        environment.update(CONTROL_DATABASE_URL=dsn, SENTINELOPS_API_TOKEN=token, SENTINELOPS_ENABLE_DEMO="false")
        stream = (output / "api.log").open("xb")
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "sentinelops.main:app",
            "--host", "127.0.0.1", "--port", str(port)], env=environment, stdout=stream, stderr=subprocess.STDOUT)
        (output / "process.json").write_text(json.dumps({"pid": server.pid, "port": port, "status": "RUNNING"}) + "\n")
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
            deadline = asyncio.get_running_loop().time() + 20
            while True:
                try:
                    if (await client.get("/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                require(server.poll() is None and asyncio.get_running_loop().time() <= deadline,
                        "native incident API did not start within its bound")
                await asyncio.sleep(.2)
            auth = {"authorization": "Bearer " + token}
            assert (await client.get("/ready/incidents")).status_code == 401
            assert (await client.get("/ready/incidents", headers=auth)).status_code == 200
            assert (await client.get("/ready")).status_code == 503
            assert (await client.post("/v1/incidents/demo")).status_code == 404
            for identifier, payloads in expected.items():
                assert len(payloads) == 4
                url = "/v1/incidents/" + identifier
                assert (await client.get(url)).status_code == 401
                current = await client.get(url, headers=auth)
                assert current.status_code == 200 and current.json() == payloads[-1] and current.headers["etag"] == '"4"'
                context = await client.get(url + "/context", headers=auth)
                assert context.status_code == 200
                body = context.json()
                assert body["revision"] == 4 and body["context"]["lifecycle_stage"] == "CLOSED"
                assert body["context"]["recovery_source"] == "natural" and body["context"]["availability"] == "ELIGIBLE"
                assert [r["lifecycle_stage"] for r in body["timeline"]["events"]] == ["OPEN", "RCA_READY", "ESCALATED", "CLOSED"]
                assert body["payload"]["state"] == "recovered" and body["payload"]["service_signals"] == []
                for revision, payload in enumerate(payloads, 1):
                    historical = await client.get(url + "/context", params={"revision": revision}, headers=auth)
                    assert historical.status_code == 200 and historical.json()["payload"] == payload
                    assert payload["detected_at"] == payloads[0]["detected_at"]
                assert (await client.get(url, headers={**auth, "if-none-match": '"4"'})).status_code == 304
                first = await client.get(url + "/context", params={"limit": 1}, headers=auth)
                assert first.status_code == 200 and first.json()["timeline"]["next_after_revision"] == 1
                second = await client.get(url + "/context", params={"after_revision": 1}, headers=auth)
                assert [v["revision"] for v in second.json()["timeline"]["events"]] == [2, 3, 4]
            found, cursor = set(), None
            for _ in range(100):
                params = {"limit": 2}
                if cursor:
                    params["cursor"] = cursor
                page = await client.get("/v1/incidents", params=params, headers=auth)
                assert page.status_code == 200
                found.update(item["incident_id"] for item in page.json()["items"])
                cursor = page.json()["next_cursor"]
                if cursor is None:
                    break
            assert cursor is None and set(expected) <= found
        assert before == await counts()
        report = {"status": "PASS", "mode": "actual-uvicorn-HTTP-real-PostgreSQL-native-incident-read-history",
            "native_incidents": len(expected), "native_versions": before["versions"], "native_outbox": before["outbox"],
            "selected_sources": sources, "authenticated_and_unauthorized_paths_verified": True,
            "current_and_all_historical_payloads_match_immutable_native_snapshots": True,
            "ETag_and_bounded_timeline_verified": True, "pagination_contains_all_native_incidents": True,
            "detected_at_immutable": True, "read_only_side_effect_counts_unchanged": True,
            "model_ready_503": True, "demo_disabled": True, "actions_allowed": False}
        (output / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
        success = True
        return report
    finally:
        if server is not None:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            (output / "process.json").write_text(json.dumps({"pid": server.pid, "status": "STOPPED", "exit_code": server.returncode}) + "\n")
        if stream is not None:
            stream.close()
        await connection.close()
        if not success:
            (output / "failure.json").write_text(json.dumps({"status": "FAIL", "observed_at": datetime.now(timezone.utc).isoformat()}) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(check(args.run, args.output))
    print("NATIVE INCIDENT HTTP ACCEPTANCE: PASS " + str(report["native_incidents"]) + " incidents")


if __name__ == "__main__":
    main()
