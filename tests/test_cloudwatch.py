"""SDK responses are explicit software fixtures, never H3 source-parity evidence."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest

from sentinelops.telemetry import cloudwatch as cw
from sentinelops.telemetry.core import build_feature_window,stable_id,timestamp
from sentinelops.telemetry.prometheus import BucketNotReady
from sentinelops.telemetry.store import ArtifactStore

END=datetime(2026,11,1,0,1,tzinfo=timezone.utc)


def catalog():
    return {"catalog_version":"cloudwatch-http-red/v1","service":"payment-service","environment":"aws-fixture",
        "log_group_arn":"arn:aws:logs:ap-southeast-1:123456789012:log-group:/sentinelops/payment",
        "namespace":"SentinelOps/HttpRed","request_metric":"BusinessRequests","error_metric":"BusinessErrors",
        "dimensions":{k:k for k in ("service","method","route","environment")},"counter_semantics":"per-request-count/v1"}


def events(n=40,*,end=END):
    begin=end-timedelta(minutes=1)
    rows=[]
    for i in range(n):
        at=cw._ms(begin)+i
        body={"timestamp_unix_ms":at,"service":"payment-service","event":"request_completed",
            "method":"POST","route":"/v1/payments/charge","duration_ms":float(i),
            "status_code":500 if i==0 else 200,"request_instance_id":f"{i:032x}",
            "correlation_id":"intentionally-reused-correlation"}
        rows.append({"eventId":str(i),"logStreamName":"pod-1","timestamp":at,"ingestionTime":at+1,
                     "message":json.dumps(body)})
    return rows


class Logs:
    meta=SimpleNamespace(region_name="ap-southeast-1")
    def __init__(self,pages):self.pages=pages;self.calls=[]
    def filter_log_events(self,**kwargs):
        self.calls.append(deepcopy(kwargs))
        return deepcopy(self.pages[len(self.calls)-1])


class Metrics:
    meta=SimpleNamespace(region_name="ap-southeast-1")
    def __init__(self,n=40,errors=1,*,end=END):
        self.calls=[];self.body={"MetricDataResults":[
            {"Id":name,"Timestamps":[end-timedelta(minutes=1)],"Values":[value],"StatusCode":"Complete"}
            for name,value in (("requests",n),("errors",errors))]}
    def get_metric_data(self,**kwargs):
        self.calls.append(deepcopy(kwargs));return deepcopy(self.body)


def capture(n=40,*,pages=None,end=END):
    logs=Logs(pages if pages is not None else [{"events":events(n,end=end)}])
    metrics=Metrics(n,int(n>0),end=end)
    source=cw.CloudWatchSource(logs,metrics,catalog(),clock=lambda:end+timedelta(seconds=120))
    return source.capture(end),logs,metrics


def decode(value):return cw.decode_capture(value,environment="aws-fixture")


def test_actual_api_query_scope_and_exact_log_distribution_rollup():
    value,logs,metrics=capture()
    item=decode(value)
    assert item["payload"]["p95_latency_ms"]==pytest.approx(37.05)
    assert item["payload"]["error_rate"]==.025 and item["measurements"]=={"request_count":40,"error_count":1}
    assert item["quality"]["status"]=="valid"
    assert logs.calls[0]["endTime"]==cw._ms(END)-1 and logs.calls[0]["unmask"] is False
    assert "request_completed" in logs.calls[0]["filterPattern"]
    for query in metrics.calls[0]["MetricDataQueries"]:
        assert query["MetricStat"]["Unit"]=="Count" and query["MetricStat"]["Period"]==60
        assert query["MetricStat"]["Stat"]=="Sum" and query["AccountId"]=="123456789012"
    assert item["available_at"]==timestamp(END+timedelta(seconds=120))
    assert item["source_profile"]!= "prometheus-increase-60s-5s/v1"


def test_empty_intermediate_page_is_not_end_of_pagination():
    value,logs,_=capture(pages=[{"events":[],"nextToken":"more"},{"events":events()}])
    assert decode(value)["measurements"]["request_count"]==40
    assert len(logs.calls)==2 and logs.calls[1]["nextToken"]=="more"


def test_both_source_event_and_application_instance_dedup_preserve_real_requests():
    rows=events()
    duplicate=deepcopy(rows[5]);duplicate["eventId"]="redelivered-app-log"
    value,_,_=capture(pages=[{"events":rows+[rows[0],duplicate]}])
    assert decode(value)["measurements"]["request_count"]==40
    # Correlation-ID reuse never collapses the forty distinct request instances.
    assert len({json.loads(r["message"])["correlation_id"] for r in rows})==1


@pytest.mark.parametrize("same_event",[True,False])
def test_conflicting_duplicates_are_rejected(same_event):
    rows=events();conflict=deepcopy(rows[0])
    if not same_event:conflict["eventId"]="other-event"
    body=json.loads(conflict["message"]);body["duration_ms"]=999
    conflict["message"]=json.dumps(body)
    value,_,_=capture(pages=[{"events":rows+[conflict]}])
    with pytest.raises(ValueError,match="conflicting duplicate"):decode(value)


@pytest.mark.parametrize("change",[
    {"requests":39},{"errors":0},{"missing":"errors"},{"missing":"requests"}])
def test_missing_or_mismatched_independent_counts_never_mean_healthy(change):
    value,_,_=capture()
    for row in value["metrics"]["response"]["MetricDataResults"]:
        key=row["Id"]
        if key in change:row["Values"]=[change[key]]
        if change.get("missing")==key:row.update(Timestamps=[],Values=[])
    item=decode(value)
    assert not item["quality"]["eligible_for_inference"] and item["payload"] is None
    assert item["quality"]["status"] in ("invalid","incomplete")


def test_no_traffic_and_insufficient_traffic_remain_unknown():
    for n,status in ((0,"no_traffic"),(2,"insufficient_traffic")):
        value,_,_=capture(n)
        item=decode(value)
        assert item["quality"]["status"]==status and item["payload"] is None


@pytest.mark.parametrize("mutation",["partial","extra-id","extra-point","negative","fraction","nan","late-clock","no-terminal","cycle","wrong-unit","wrong-route"])
def test_bad_metrics_pages_units_and_query_bindings_are_rejected(mutation):
    value,_,_=capture()
    body=value["metrics"]["response"];row=body["MetricDataResults"][0]
    if mutation=="partial":row["StatusCode"]="PartialData"
    elif mutation=="extra-id":body["MetricDataResults"].append(deepcopy(row))
    elif mutation=="extra-point":row["Values"].append(2);row["Timestamps"].append(row["Timestamps"][0])
    elif mutation in ("negative","fraction","nan"):row["Values"]=[{"negative":-1,"fraction":.5,"nan":float("nan")}[mutation]]
    elif mutation=="late-clock":value["logs"][0]["response"]["events"][0]["ingestionTime"]=cw._ms(END+timedelta(hours=1))
    elif mutation=="no-terminal":value["logs"][0]["response"]["nextToken"]="still-more"
    elif mutation=="cycle":
        page=deepcopy(value["logs"][0]);page["response"]["nextToken"]="same"
        value["logs"][0]["response"]["nextToken"]="same"
        page["request"]["nextToken"]="same";value["logs"].append(page)
    elif mutation=="wrong-unit":value["metrics"]["request"]["MetricDataQueries"][0]["MetricStat"]["Unit"]="Seconds"
    else:value["logs"][0]["request"]["filterPattern"]="anything"
    with pytest.raises(ValueError):decode(value)


@pytest.mark.parametrize("mutation",["bool-count","bool-duration","wrong-status","wrong-service","wrong-time","bad-id","duplicate-key","nonfinite","bad-json"])
def test_malformed_completion_cannot_supply_valid_p95(mutation):
    value,_,_=capture();event=value["logs"][0]["response"]["events"][0]
    body=json.loads(event["message"])
    if mutation=="bool-count":event["timestamp"]=True
    elif mutation=="bool-duration":body["duration_ms"]=True
    elif mutation=="wrong-status":body["status_code"]=600
    elif mutation=="wrong-service":body["service"]="inventory-service"
    elif mutation=="wrong-time":body["timestamp_unix_ms"]+=1
    elif mutation=="bad-id":body.pop("request_instance_id")
    event["message"]=json.dumps(body)
    if mutation=="duplicate-key":event["message"]=event["message"][:-1]+',"status_code":200}'
    if mutation=="nonfinite":event["message"]=event["message"].replace('"duration_ms": 0.0','"duration_ms": NaN')
    if mutation=="bad-json":event["message"]="not-json"
    with pytest.raises(ValueError):decode(value)


def test_watermark_and_transport_failure_do_not_call_cloud_or_leak_sdk_errors():
    logs=Logs([]);metrics=Metrics()
    source=cw.CloudWatchSource(logs,metrics,catalog(),clock=lambda:END)
    with pytest.raises(BucketNotReady):source.capture(END)
    assert logs.calls==metrics.calls==[]
    class FailedLogs:
        meta=SimpleNamespace(region_name="ap-southeast-1")
        def filter_log_events(self,**kwargs):raise RuntimeError("not-a-real-secret credential=do-not-retain")
    value=cw.CloudWatchSource(FailedLogs(),metrics,catalog(),
        clock=lambda:END+timedelta(seconds=120)).capture(END)
    assert value["source_error"]=={"error_type":"RuntimeError"}
    assert "do-not-retain" not in json.dumps(value)
    with pytest.raises(ValueError,match="unavailable"):decode(value)


def test_capture_budget_and_nonadvancing_token_fail_closed(monkeypatch):
    monkeypatch.setattr(cw,"MAX_PAGES",2)
    for pages in ([{"events":[],"nextToken":"a"},{"events":[],"nextToken":"a"}],
                  [{"events":[],"nextToken":"a"},{"events":[],"nextToken":"b"}]):
        value,logs,metrics=capture(pages=pages)
        assert value["source_error"] and len(logs.calls)==2 and not metrics.calls
        with pytest.raises(ValueError):decode(value)
    monkeypatch.setattr(cw,"MAX_BYTES",10)
    value,_,metrics=capture()
    assert value["source_error"] and not metrics.calls


def test_replay_uses_receipt_cutoff_and_preserves_raw_refs_and_quarantine(tmp_path):
    store=ArtifactStore(tmp_path/"output")
    captures=[capture(end=END+timedelta(minutes=i))[0] for i in range(5)]
    bad=deepcopy(captures[0]);bad["source_error"]={"error_type":"TimeoutError"}
    result=cw.process_captures(captures+[captures[0],bad],store,environment="aws-fixture")
    assert len(result["raw_refs"])==6 and result["observation_count"]==5 and len(result["quarantine_refs"])==1
    observations=[store.read(ref) for ref in result["observation_refs"]]
    for point in observations:
        assert all(ref in result["raw_refs"] for ref in point["provenance"]["source_refs"])
    cutoff=END+timedelta(minutes=4)
    early=build_feature_window(observations,service="payment-service",window_end=cutoff,as_of=cutoff,
        environment="aws-fixture",source_profile=cw.SOURCE_PROFILE)
    assert early["payload"] is None  # 120s arrival lag leaves only three of five buckets.
    later=build_feature_window(observations,service="payment-service",window_end=cutoff,as_of=cutoff+timedelta(seconds=120),
        environment="aws-fixture",source_profile=cw.SOURCE_PROFILE)
    assert later["quality"]["eligible_for_inference"] and later["payload"]["features"]["request_rate_mean_5m"]==pytest.approx(40/60)


def test_offline_cli_preserves_source_and_requires_exclusive_output(tmp_path):
    store=ArtifactStore(tmp_path/"source");value,_,_=capture();ref=store.write("raw",value)
    cmd=[sys.executable,"-m","scripts.replay_cloudwatch","--store",str(store.root),"--capture-ref",ref,
         "--environment","aws-fixture","--output",str(tmp_path/"output")]
    completed=subprocess.run(cmd,cwd=Path(__file__).parents[1],capture_output=True,text=True,timeout=30)
    assert completed.returncode==0
    result=json.loads(completed.stdout);assert result["observations"]==1 and result["source_parity"]=="not-accepted"
    assert not result["activation_allowed"] and store.read(ref)==value
    repeated=subprocess.run(cmd,cwd=Path(__file__).parents[1],capture_output=True,text=True,timeout=30)
    assert repeated.returncode!=0


@pytest.mark.parametrize("change",[
    {"counter_semantics":"cumulative-counter"},{"request_metric":"BusinessErrors"},
    {"namespace":"AWS/ELB"},{"log_group_arn":"not-an-arn"},{"service":"rds"}])
def test_catalog_is_explicit_and_cannot_substitute_resource_or_cumulative_metrics(change):
    with pytest.raises(ValueError):cw.CloudWatchSource(None,None,{**catalog(),**change})


@pytest.mark.parametrize("message",["null","[]","123"])
def test_nonobject_completion_is_quarantined_without_aborting_replay(tmp_path,message):
    value,_,_=capture();value["logs"][0]["response"]["events"][0]["message"]=message
    result=cw.process_captures([value],ArtifactStore(tmp_path),environment="aws-fixture")
    assert result["observation_count"]==0 and len(result["quarantine_refs"])==1


@pytest.mark.parametrize("kind",["log-error","metric-error","false-source-error","bool-query"])
def test_error_markers_and_noncanonical_query_types_are_never_ignored(kind):
    value,_,_=capture()
    if kind=="log-error":value["logs"][0]["response"]["Error"]={"Code":"Failure"}
    elif kind=="metric-error":value["metrics"]["response"]["Error"]={"Code":"Failure"}
    elif kind=="false-source-error":value["source_error"]=None
    else:value["metrics"]["request"]["MetricDataQueries"][0]["ReturnData"]=1
    with pytest.raises(ValueError):decode(value)


def test_cloud_profile_cannot_reach_a_local_model_detector():
    from sentinelops.ml.registry import ModelRegistry,IncompatibleFeatures
    from sentinelops.telemetry.core import FEATURE_KEYS
    registry=ModelRegistry()
    class ForbiddenDetector:
        def predict(self,*args):pytest.fail("incompatible cloud profile reached local estimator")
    registry._models["payment-service"]=({"ordered_features":list(FEATURE_KEYS),
        "provenance":{"source_profile":"prometheus-increase-60s-5s/v1"}},ForbiddenDetector(),None,"fixture-only")
    feature={"schema_version":"1.0","service":"payment-service","window_start":timestamp(END-timedelta(minutes=5)),
        "window_end":timestamp(END),"features":{key:1.0 for key in FEATURE_KEYS}}
    with pytest.raises(IncompatibleFeatures,match="source schema differs"):
        registry.infer(feature,source_profile=cw.SOURCE_PROFILE,now=END+timedelta(seconds=120))


def test_cross_region_metric_client_is_rejected_before_any_cloud_call():
    logs,metrics=Logs([]),Metrics()
    metrics.meta=SimpleNamespace(region_name="us-east-1")
    with pytest.raises(ValueError,match="regions differ"):
        cw.CloudWatchSource(logs,metrics,catalog())
    assert logs.calls==metrics.calls==[]


def test_one_service_cannot_mix_metric_catalogs_in_a_feature_manifest(tmp_path):
    first,_,_=capture();second=deepcopy(first)
    second["catalog"]["request_metric"]="OtherRequests"
    second["catalog_sha256"]=stable_id(second["catalog"])
    with pytest.raises(ValueError,match="mix CloudWatch catalogs"):
        cw.process_captures([first,second],ArtifactStore(tmp_path),environment="aws-fixture")
    assert not (tmp_path/"features").exists()


def test_nonobject_raw_capture_is_quarantined(tmp_path):
    result=cw.process_captures([[]],ArtifactStore(tmp_path),environment="aws-fixture")
    assert result["observation_count"]==0 and len(result["quarantine_refs"])==1
