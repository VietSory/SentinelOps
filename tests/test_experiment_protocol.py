from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.experiments.datasets import assemble_dataset
from sentinelops.experiments.labels import feature_vector, label_feature, measure_slo
from sentinelops.telemetry.core import BUSINESS_ROUTES, BucketMetrics, build_feature_window, normalize_bucket, timestamp
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import SOURCE_PROFILE,queries
from test_prometheus_capture import capture
from scripts.build_dataset import load_run

START = datetime(2026,10,26,tzinfo=timezone.utc)
SAMPLE = Path(__file__).resolve().parents[1]/"samples"/"experiment-run.sample.json"


def run_fixture(identifier="one", split="train", offset=0, scenario="S0"):
    run = json.loads(SAMPLE.read_text())
    run["run_id"], run["session_id"], run["declared_split"] = identifier, "session-"+identifier, split
    run["source_manifest_ref"] = "fixture://source-manifest/"+identifier
    for key in ("started_at","ended_at","warmup_end_at"):
        run[key] = timestamp(utc_time(run[key])+timedelta(minutes=offset))
    run["phase_intervals"] = []
    run["scenario_id"] = scenario
    if scenario != "S0":
        run["fault_start_at"] = timestamp(START+timedelta(minutes=offset+8))
        run["recovery_start_at"] = timestamp(START+timedelta(minutes=offset+16))
        run["root_cause_ground_truth"] = "payment-service"
        run["affected_services_ground_truth"] = ["payment-service","checkout-service"]
        run["fault_method"] = "gradual-latency" if scenario == "S3" else "sandbox-error"
        for service in run["affected_services_ground_truth"]:
            run["slo_by_service"][service] = {
                "first_violation_at":timestamp(START+timedelta(minutes=offset+14)),
                "confirmed_at":timestamp(START+timedelta(minutes=offset+16)),
                "request_count":1000,
            }
    return run


def feature_fixture(run, minute=10, service="payment-service", bound=100):
    end = utc_time(run["started_at"])+timedelta(minutes=minute)
    items=[]
    for i in range(5):
        time = end-timedelta(minutes=4-i)
        items.append(normalize_bucket(BucketMetrics(service,time,120,0,((bound,120),(float('inf'),120)),1,
            time+timedelta(seconds=30),(f"fixture://{run['run_id']}/{service}/{i}",),run["source_profile"])))
    return build_feature_window(items,service=service,window_end=end,as_of=end+timedelta(seconds=30),
                                source_profile=run["source_profile"])


def input_fixture(run, **kwargs):
    return {"run":run,"features":[feature_fixture(run,**kwargs)],"truth_ref":"fixture://truth/"+run["run_id"]}


def completed_logs(minute, *, status=200, duration=10., count=40, route="/v1/payments/charge"):
    return [{"service":"payment-service","event":"request_completed","method":"POST","route":route,
             "timestamp_unix_ms":int((START.timestamp()+minute*60+i)*1000),"status_code":status,
             "duration_ms":duration,"request_instance_id":f"{minute}-{i}"} for i in range(count)]


def test_slo_truth_uses_unsampled_requests_dedup_persistence_and_excludes_control_routes():
    logs = completed_logs(0)+completed_logs(1,duration=1000)+completed_logs(2,status=503)
    logs += deepcopy(logs) + completed_logs(3,status=500,route="/internal/faults")
    result = measure_slo(logs,"payment-service")
    assert result["request_count"] == 120
    assert result["first_violation_at"] == timestamp(START+timedelta(minutes=1))
    assert result["confirmed_at"] == timestamp(START+timedelta(minutes=3))
    gap = measure_slo(completed_logs(0,status=500)+completed_logs(2,status=500),"payment-service")
    assert gap["first_violation_at"] is None
    partial = measure_slo(completed_logs(0,status=500)+completed_logs(1,status=500),"payment-service",start_at=START+timedelta(seconds=10))
    assert partial["first_violation_at"] is None


def test_conflicting_ground_truth_logs_and_invalid_durations_fail():
    logs = completed_logs(0)
    other = deepcopy(logs[0]); other["status_code"] = 503
    with pytest.raises(ValueError,match="conflicting"):
        measure_slo(logs+[other],"payment-service")
    logs[0]["duration_ms"] = float("nan")
    with pytest.raises(ValueError,match="duration"):
        measure_slo(logs,"payment-service")


def test_gradual_label_uses_service_scope_and_stops_before_incident():
    run = run_fixture(scenario="S3")
    assert label_feature(feature_fixture(run),run)["risk_label"] == 1
    assert label_feature(feature_fixture(run,service="inventory-service"),run)["risk_label"] == 0
    assert label_feature(feature_fixture(run,minute=15),run)["risk_label"] is None
    assert label_feature(feature_fixture(run,minute=18),run)["risk_label"] is None
    abrupt = run_fixture(scenario="S1")
    assert label_feature(feature_fixture(abrupt),abrupt)["risk_label"] is None
    assert label_feature(feature_fixture(run,minute=4),run) is None


def test_model_vector_accepts_only_registered_finite_features():
    run = run_fixture(); features = feature_fixture(run)["payload"]["features"]
    assert len(feature_vector(features)) == 5
    for key in ("scenario_id","run_id","fault_start_at","label"):
        with pytest.raises(ValueError,match="metadata/labels"):
            feature_vector({**features,key:1})


def test_dataset_pilot_is_explicit_and_id_cannot_be_rebound(tmp_path):
    item = input_fixture(run_fixture())
    store = ArtifactStore(tmp_path)
    with pytest.raises(ValueError,match="not training-ready"):
        assemble_dataset([item],store,dataset_id="pilot")
    first = assemble_dataset([item],store,dataset_id="pilot",allow_pilot=True,now=START+timedelta(hours=1))
    assert first["status"] == "pilot" and not first["training_ready"]
    assert first["blocking_reasons"]
    second = assemble_dataset([item],store,dataset_id="pilot",allow_pilot=True,now=START+timedelta(days=1))
    assert first == second
    table = store.read(first["feature_rows_ref"])
    assert table["rows"][0]["risk_label"] == 0
    assert "run_id" not in table["rows"][0]["features"]
    changed = input_fixture(run_fixture(),bound=200)
    with pytest.raises(ValueError,match="already frozen"):
        assemble_dataset([changed],store,dataset_id="pilot",allow_pilot=True,now=START+timedelta(hours=1))
    with pytest.raises(ValueError,match="before its runs complete"):
        assemble_dataset([item],store,dataset_id="too-early",allow_pilot=True,now=START)


@pytest.mark.parametrize("fault",["session","time","source","dirty","copied_feature"])
def test_dataset_rejects_leakage_and_unreproducible_inputs(tmp_path,fault):
    first = run_fixture("a")
    second = run_fixture("b",split="validation",offset=30)
    inputs = [input_fixture(first),input_fixture(second)]
    if fault == "session": second["session_id"] = first["session_id"]
    elif fault == "time":
        first = run_fixture("a",offset=60); inputs[0] = input_fixture(first)
    elif fault == "source":
        second["source_profile"] = "prometheus-increase-60s-5s/v1";inputs[1] = input_fixture(second)
    elif fault == "dirty": second["dirty_worktree"] = True
    else:
        second = run_fixture("b");inputs[1] = input_fixture(second)
        inputs[1]["features"] = deepcopy(inputs[0]["features"])
    with pytest.raises(ValueError):
        assemble_dataset(inputs,ArtifactStore(tmp_path),dataset_id="invalid",allow_pilot=True)


def test_completed_run_cannot_hide_missing_capture_or_short_warmup():
    run = run_fixture()
    for key,value in (("source_manifest_ref",None),("warmup_end_at",timestamp(START+timedelta(seconds=10)))):
        invalid = deepcopy(run);invalid[key] = value
        with pytest.raises(ValueError): validate_payload("experiment-run.schema.json",invalid)


def stored_run_fixture(tmp_path):
    run = run_fixture()
    run["source_profile"] = SOURCE_PROFILE
    source = ArtifactStore(tmp_path/"serving")
    records = []
    trace_id = "1"*32
    for service,(method,route) in BUSINESS_ROUTES.items():
        for minute in range(20):
            for record in completed_logs(minute,route=route):
                record["service"],record["method"] = service,method
                record["request_instance_id"] = service+"-"+record["request_instance_id"]
                record["trace_id"] = trace_id
                record["correlation_id"] = "fixture-correlation"
                record["span_id"] = {"checkout-service":"1","inventory-service":"3","payment-service":"5"}[service].zfill(16)
                records.append(record)
        run["slo_by_service"][service]["request_count"] = 800
    run["logs_ref"] = source.write("raw",{"kind":"serving-logs","records":records})
    captures=[]
    for minute in range(1,21):
        for service in BUSINESS_ROUTES:
            raw=capture(START+timedelta(minutes=minute));raw["service"]=service
            for key,expression in queries(service).items(): raw["queries"][key]["expression"]=expression
            raw["queries"]["errors"]["response"]["data"]["result"][0]["value"][1]="0"
            captures.append(raw)
    base=process_captures(captures,source)
    resources=[]
    for service,(method,route) in BUSINESS_ROUTES.items():
        index,parent={"checkout-service":(1,9),"inventory-service":(3,2),"payment-service":(5,4)}[service]
        attrs=[{"key":key,"value":{"stringValue":value}} for key,value in (("http.request.method",method),("http.route",route))]
        attrs.extend([{"key":"http.response.status_code","value":{"intValue":"200"}},
                      {"key":"sentinelops.correlation_id","value":{"stringValue":"fixture-correlation"}}])
        spans=[{"traceId":trace_id,"spanId":str(index).zfill(16),"parentSpanId":str(parent).zfill(16),"kind":2,"attributes":attrs}]
        if service=="checkout-service":
            for client_id,dependency in ((2,"inventory-service"),(4,"payment-service")):
                spans.append({"traceId":trace_id,"spanId":str(client_id).zfill(16),"parentSpanId":"1".zfill(16),"kind":3,
                    "attributes":[{"key":"http.response.status_code","value":{"intValue":"200"}},
                                  {"key":"peer.service","value":{"stringValue":dependency}}]})
        resources.append({"resource":{"attributes":[{"key":"service.name","value":{"stringValue":service}}]},
            "scopeSpans":[{"spans":spans}]})
    trace=source.write("raw",{"kind":"trace","trace_id":trace_id,"body":json.dumps({"result":{"resourceSpans":resources}})})
    run["source_manifest_ref"]=source.write("manifests",{**{k:v for k,v in base.items() if k!="manifest_ref"},
        "normalization_manifest_ref":base["manifest_ref"],"evidence_refs":{"logs":run["logs_ref"],"traces":[trace],"infrastructure":[]}})
    run["infrastructure_refs"]=[]
    save_run(tmp_path,run)
    return run,source


def save_run(path,run):
    truth=ArtifactStore(path/"truth")
    truth.write("manifests",run)
    (path/"run.json").write_text(json.dumps(run))


def test_run_loader_recomputes_labels_from_immutable_server_log_capture(tmp_path):
    run,source=stored_run_fixture(tmp_path)
    loaded=load_run(tmp_path)
    assert len(loaded["features"])==60
    assert all(feature["source_profile"]==SOURCE_PROFILE for feature in loaded["features"])
    run["slo_by_service"]["payment-service"]["request_count"] = 801
    save_run(tmp_path,run)
    with pytest.raises(ValueError,match="labels do not match"):
        load_run(tmp_path)


@pytest.mark.parametrize("fault",["missing_raw","fabricated_feature","control_truth","probe_trace","disconnected_trace"])
def test_run_loader_requires_reproducible_raw_lineage_and_business_evidence(tmp_path,fault):
    run,store=stored_run_fixture(tmp_path)
    manifest=store.read(run["source_manifest_ref"])
    if fault=="missing_raw":
        (store.root/manifest["raw_refs"][0].removeprefix("artifact://")).unlink()
    elif fault=="fabricated_feature":
        reference=next(ref for ref in manifest["feature_refs"] if store.read(ref)["payload"])
        fabricated=store.read(reference)
        fabricated["payload"]["features"]["request_rate_mean_5m"]+=100
        validate_payload("feature-envelope.schema.json",fabricated)
        manifest["feature_refs"].remove(reference)
        manifest["feature_refs"].append(store.write("features",fabricated))
    elif fault=="control_truth":
        raws=[store.read(ref) for ref in manifest["raw_refs"]]
        raws[0]["scenario_id"]="S3"
        replay=process_captures(raws,store)
        manifest={**{k:v for k,v in replay.items() if k!="manifest_ref"},
            "normalization_manifest_ref":replay["manifest_ref"],"evidence_refs":manifest["evidence_refs"]}
    else:
        raw=store.read(manifest["evidence_refs"]["traces"][0])
        body=json.loads(raw["body"])
        for resource in body["result"]["resourceSpans"]:
            for tag in resource["scopeSpans"][0]["spans"][0]["attributes"]:
                if tag["key"]=="http.route" and fault=="probe_trace": tag["value"]["stringValue"]="/ready"
        if fault=="disconnected_trace":
            body["result"]["resourceSpans"][-1]["scopeSpans"][0]["spans"][0]["parentSpanId"]="1".zfill(16)
        raw["body"]=json.dumps(body)
        manifest["evidence_refs"]["traces"]=[store.write("raw",raw)]
    run["source_manifest_ref"]=store.write("manifests",manifest)
    save_run(tmp_path,run)
    with pytest.raises(ValueError): load_run(tmp_path)
