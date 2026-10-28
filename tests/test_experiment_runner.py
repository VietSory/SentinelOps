import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from sentinelops.experiments import runner
from sentinelops.telemetry.core import BUSINESS_ROUTES
from sentinelops.telemetry.prometheus import PrometheusSource
from sentinelops.telemetry.store import ArtifactStore


@pytest.mark.parametrize("changes", [
    {"scenario":"S2"}, {"scenario":"S3","fault_mode":"error"}, {"split":"random"},
    {"rps":True}, {"rps":float("nan")}, {"rps":float("inf")}, {"rps":11},
    {"warmup_seconds":299}, {"active_seconds":119}, {"seed":True},
    {"scenario":"S1","recovery_seconds":0}, {"maximum_delay_ms":4000}, {"scenario":"S5","split":"train"},
])
def test_runner_rejects_unsupported_or_unbounded_experiments(changes):
    with pytest.raises(ValueError):
        replace(runner.ExperimentConfig("S0","train"),**changes).validate()


def test_experiment_lock_prevents_overlap_and_releases_on_failure(tmp_path):
    with pytest.raises(ValueError):
        with runner.ExperimentLock(tmp_path):
            with pytest.raises(RuntimeError,match="sandbox lock"):
                with runner.ExperimentLock(tmp_path):
                    pytest.fail("overlapping experiment was admitted")
            raise ValueError("simulated run failure")
    with runner.ExperimentLock(tmp_path):
        pass


@pytest.mark.parametrize("interval,accepted",[("5s",True),("15s",False),(None,False)])
def test_source_profile_verifies_actual_prometheus_configuration(interval,accepted):
    text = "global:\n  scrape_interval: 15s\nscrape_configs:\n- job_name: otel-collector\n"
    if interval:
        text += "  scrape_interval: "+interval+"\n"
    source = PrometheusSource(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
        "status":"success","data":{"yaml":text}})))
    try:
        if accepted:
            assert source.verify_configuration()["scrape_interval"] == "5s"
        else:
            with pytest.raises(ValueError,match="source profile"):
                source.verify_configuration()
    finally:
        source.close()


def test_log_admission_excludes_controls_and_keeps_business_evidence(monkeypatch,tmp_path):
    method,route = BUSINESS_ROUTES["payment-service"]
    logs = [
        {"service":"payment-service","event":"request_completed","method":method,"route":route},
        {"service":"payment-service","event":"request_completed","method":"POST","route":"/internal/faults"},
        {"service":"payment-service","event":"fault_configuration_changed","mode":"error"},
        {"service":"payment-service","event":"payment_approved"},
        {"service":"payment-service","event":"request_started","path":"/internal/faults"},
    ]
    monkeypatch.setattr(runner.subprocess,"run",lambda *args,**kwargs:SimpleNamespace(stdout="\n".join(map(json.dumps,logs))))
    full,serving = runner.serving_logs(tmp_path,"2026-10-26T00:00:00Z")
    assert full == logs
    assert serving == [logs[0],logs[3]]


@pytest.mark.parametrize('scenario,split',[('S0','train'),('S5','validation')])
def test_failed_preflight_restores_both_faults_and_saves_failed_evidence(monkeypatch,tmp_path,scenario,split):
    if scenario=='S5':
        (tmp_path/'configs').mkdir()
        (tmp_path/'configs/s5_development_protocol.json').write_bytes((Path(__file__).resolve().parents[1]/'configs/s5_development_protocol.json').read_bytes())
    monkeypatch.setattr(runner,"checkout_identity",lambda project:("a"*40,False,{}))
    monkeypatch.setattr(runner,"image_identity",lambda project:{service:"sha256:"+"b"*64 for service in BUSINESS_ROUTES})
    monkeypatch.setattr(runner,"serving_logs",lambda *args:([],[]))
    originals = {runner.PAYMENT:{"mode":"latency","delay_ms":123},runner.INVENTORY:{"mode":"error","delay_ms":0}}
    posts = []
    def handle(request):
        base = str(request.url).removesuffix("/internal/faults")
        if request.method == "GET" and request.url.path == "/internal/faults":
            return httpx.Response(200,json=originals[base])
        if request.method == "POST":
            value = json.loads(request.content)
            posts.append((base,value))
            # Fail after both originals have been captured and Inventory changed.
            if len(posts)==2:
                return httpx.Response(503,json={"detail":"preflight failure"})
        return httpx.Response(200,json={"status":"ready"})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(runner.httpx,"AsyncClient",lambda **kwargs:real_client(transport=httpx.MockTransport(handle),**kwargs))
    closed = []
    monkeypatch.setattr(runner.PrometheusSource,"verify_configuration",lambda self:{"scrape_interval":"5s"})
    monkeypatch.setattr(runner.PrometheusSource,"close",lambda self:closed.append(True))
    with pytest.raises(RuntimeError,match="partial evidence saved"):
        asyncio.run(runner.execute(runner.ExperimentConfig(scenario,split),project=tmp_path,output=tmp_path/"runs"))
    assert posts[-2:] == list(originals.items())
    assert closed == [True]
    root = next((tmp_path/"runs").iterdir())
    run = json.loads((root/"run.json").read_text())
    assert run["status"] == "failed" and run["failure_reason"] == "HTTPStatusError"
    assert run["declared_split"] == split and run["warmup_end_at"] is None
    if scenario=='S5':
        assert run['root_cause_ground_truth'] is None and set(run['root_causes_ground_truth'])=={'payment-service','inventory-service'}
        assert set(run['parameters']['traffic_plan'])==set(BUSINESS_ROUTES)
    assert ArtifactStore(root/"serving").read(run["logs_ref"])["records"] == []
    assert ArtifactStore(root/"truth").read(run["request_outcomes_ref"])["outcomes"] == []


def test_dirty_checkout_is_rejected_before_docker_or_run_creation(monkeypatch,tmp_path):
    monkeypatch.setattr(runner,"checkout_identity",lambda project:("a"*40,True,{}))
    def docker_must_not_run(project):
        pytest.fail("dirty source reached Docker preflight")
    monkeypatch.setattr(runner,"image_identity",docker_must_not_run)
    with pytest.raises(ValueError,match="commit source first"):
        asyncio.run(runner.execute(runner.ExperimentConfig("S0","train"),project=tmp_path,output=tmp_path/"runs"))
    assert not (tmp_path/"runs").exists()
