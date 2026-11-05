"""Native API/log fixtures prove supported structure only, never AWS/RCA readiness."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from sentinelops.telemetry import xray
from sentinelops.telemetry import cloudwatch as cw
from sentinelops.telemetry.core import BUSINESS_ROUTES,timestamp
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.trace_evidence import validate_trace
from test_cloudwatch import END,Logs,Metrics,catalog,events

TRACE="ffffffff1e8720b39541901950019ee5"
XRAY="1-"+TRACE[:8]+"-"+TRACE[8:]
CORRELATION="same-observed-request"
ROOT,PAYCLIENT,PAY,INVCLIENT,INV,DB=[str(i)*16 for i in range(1,7)]
ASOF=END+timedelta(seconds=150)


def native_json(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,dict):return "{"+",".join(json.dumps(k)+":"+native_json(v) for k,v in value.items())+"}"
    if isinstance(value,list):return "["+",".join(native_json(v) for v in value)+"]"
    return json.dumps(value)


def documents(*,direct=False,independent=False):
    base=Decimal(cw._ms(END-timedelta(minutes=1)))/1000
    def server(service,identity,start,end,parent=None,children=None):
        row={"trace_id":XRAY,"id":identity,"name":service,"start_time":base+Decimal(start),"end_time":base+Decimal(end),
            "http":{"request":{"method":BUSINESS_ROUTES[service][0]},"response":{"status":200}},
            "metadata":{"do-not-export":"private-source-fixture"}}
        if parent:row["parent_id"]=parent
        if children:row["subsegments"]=children
        return row
    pc={"id":PAYCLIENT,"name":"opaque.remote.host","namespace":"remote","start_time":base-Decimal("1.5"),
        "end_time":base+Decimal(".8"),"http":{"request":{"method":"POST","url":"https://private-fixture.example/path"},
                                          "response":{"status":200}}}
    ic={**deepcopy(pc),"id":INVCLIENT,"http":{"request":{"method":"GET"},"response":{"status":200}}}
    if direct:return [server("payment-service",PAY,"-1",".5")]
    root=server("checkout-service",ROOT,"-1.999999991","1",children=[pc,ic])
    pay=server("payment-service",PAY,"-1",".5",PAYCLIENT)
    inv=server("inventory-service",INV,"-.7",".3",INVCLIENT)
    if independent:
        root["subsegments"]=[ic]
        pc.update(type="subsegment",trace_id=XRAY,parent_id=ROOT)
        return [root,pay,inv,pc]
    return [root,pay,inv]


def completion(service):
    config=catalog();config["service"]=service
    rows=events()
    method,route=BUSINESS_ROUTES[service]
    for item in rows:
        body=json.loads(item["message"]);body.update(service=service,method=method,route=route)
        if item["eventId"]=="1":
            body.update(trace_id=TRACE,span_id={"checkout-service":ROOT,"payment-service":PAY,"inventory-service":INV}[service],
                        correlation_id=CORRELATION)
        item["message"]=json.dumps(body)
    return cw.CloudWatchSource(Logs([{"events":rows}]),Metrics(),config,
        clock=lambda:END+timedelta(seconds=120)).capture(END)


class Client:
    meta=SimpleNamespace(region_name="ap-southeast-1")
    def __init__(self,pages):self.pages=pages;self.calls=[]
    def batch_get_traces(self,**kwargs):
        self.calls.append(deepcopy(kwargs));return deepcopy(self.pages[len(self.calls)-1])


def fixture(*,direct=False,independent=False,pages=None):
    docs=documents(direct=direct,independent=independent)
    body={"Traces":[{"Id":XRAY,"LimitExceeded":False,"Segments":[{"Id":d["id"],"Document":native_json(d)} for d in docs]}],
          "UnprocessedTraceIds":[]}
    client=Client(pages if pages is not None else [body])
    source=xray.XRaySource(client,region="ap-southeast-1",account_id="123456789012",environment="aws-fixture",
                           clock=lambda:END+timedelta(seconds=130))
    capture=source.capture(TRACE,request_entry_service="payment-service" if direct else "checkout-service")
    logs=[completion(service) for service in (["payment-service"] if direct else BUSINESS_ROUTES)]
    return capture,logs,client


def normalize(capture,logs,as_of=ASOF):
    return xray.normalize_capture(capture,logs,environment="aws-fixture",correlation_id=CORRELATION,as_of=as_of)


def edit(capture,index,change):
    item=capture["pages"][0]["response"]["Traces"][0]["Segments"][index]
    doc=json.loads(item["Document"],parse_float=Decimal)
    change(doc)
    item["Document"]=native_json(doc)


def test_exact_document_time_native_links_log_identity_and_no_authority():
    capture,logs,client=fixture()
    result=normalize(capture,logs)
    assert client.calls==[{"TraceIds":[XRAY]}]
    trace=result["trace_payload"]["data"][0]
    root=next(s for s in trace["spans"] if s["spanID"]==ROOT)
    base=cw._ms(END-timedelta(minutes=1))*1000000
    assert root["timing"]["start_unix_ns"]==base-2000000000+9
    assert len(trace["spans"])==5 and len(trace["processes"])==3
    validate_trace(result["trace_payload"],TRACE,CORRELATION,{s:200 for s in BUSINESS_ROUTES},require_timing=True)
    assert result["available_at"]==timestamp(END+timedelta(seconds=130))
    assert result["structural_request_validated"] and result["source_parity"]=="not-accepted"
    assert not result["actions_allowed"] and result["clock_uncertainty_ns"] is None
    assert not result["earliest_backend_availability_verified"]
    assert "private-source-fixture" not in json.dumps(result) and "private-fixture.example" not in json.dumps(result)


def test_independent_subsegments_preserve_the_same_supported_payload():
    nested,logs,_=fixture()
    independent,_,_=fixture(independent=True)
    assert normalize(nested,logs)["trace_payload"]==normalize(independent,logs)["trace_payload"]


def test_direct_request_requires_original_capture_scope_and_no_cascade_logs():
    capture,logs,_=fixture(direct=True)
    result=normalize(capture,logs)
    assert result["request_entry_service"]=="payment-service"
    assert len(result["trace_payload"]["data"][0]["spans"])==1
    capture["request_entry_service"]="checkout-service"
    with pytest.raises(xray.TraceUnavailable):normalize(capture,logs)
    capture["request_entry_service"]="payment-service"
    with pytest.raises(ValueError,match="cascade logs"):normalize(capture,logs+[completion("checkout-service")])


@pytest.mark.parametrize("problem",["in-progress","truncated","unprocessed","no-result","foreign","outer-id","duplicate","cycle","nested-parent","missing-parent","inferred","server-name","client-status","no-native-error","bad-log-clock","no-end"])
def test_partial_inferred_conflicting_or_unsupported_data_never_becomes_supported_trace(problem):
    capture,logs,_=fixture()
    trace=capture["pages"][0]["response"]["Traces"][0]
    if problem=="in-progress":edit(capture,0,lambda d:d.update(in_progress=True))
    elif problem=="truncated":trace["LimitExceeded"]=True
    elif problem=="unprocessed":capture["pages"][0]["response"]["UnprocessedTraceIds"]=[XRAY]
    elif problem=="no-result":capture["pages"][0]["response"]["Traces"]=[]
    elif problem=="foreign":trace["Id"]="1-12345678-"+"a"*24
    elif problem=="outer-id":trace["Segments"][0]["Id"]="a"*16
    elif problem=="duplicate":trace["Segments"].append(deepcopy(trace["Segments"][0]))
    elif problem=="cycle":edit(capture,0,lambda d:d.update(parent_id=PAYCLIENT))
    elif problem=="nested-parent":edit(capture,0,lambda d:d["subsegments"][0].update(parent_id="a"*16))
    elif problem=="missing-parent":edit(capture,1,lambda d:d.update(parent_id="a"*16))
    elif problem=="inferred":edit(capture,1,lambda d:d.update(inferred=True))
    elif problem=="server-name":edit(capture,1,lambda d:d.update(name="inventory-service"))
    elif problem=="client-status":edit(capture,0,lambda d:d["subsegments"][0]["http"]["response"].update(status=500))
    elif problem=="no-native-error":
        # All three independent facts agree on 500, but no native fault flag is supplied.
        for row in logs[0]["logs"][0]["response"]["events"]:
            body=json.loads(row["message"])
            if body.get("span_id")==ROOT:body["status_code"]=500;row["message"]=json.dumps(body)
        logs[0]["metrics"]["response"]["MetricDataResults"][1]["Values"]=[2]
        edit(capture,0,lambda d:d["http"]["response"].update(status=500))
    elif problem=="bad-log-clock":edit(capture,0,lambda d:d.update(start_time=Decimal(cw._ms(END))/1000))
    else:edit(capture,0,lambda d:d.pop("end_time"))
    with pytest.raises((ValueError,KeyError)):normalize(capture,logs)


@pytest.mark.parametrize("encoded",["NaN","-1","true","1e99","1793491200.0000000001",'"1793491200.1"'])
def test_timing_never_rounds_floats_or_invents_zero(encoded):
    capture,logs,_=fixture()
    item=capture["pages"][0]["response"]["Traces"][0]["Segments"][0]
    item["Document"]=item["Document"].replace('"start_time":1793491198.000000009','"start_time":'+encoded)
    # Fixture epoch is checked explicitly so a no-op replacement cannot make this test pass.
    assert '"start_time":'+encoded in item["Document"]
    with pytest.raises(ValueError):normalize(capture,logs)


def test_receipts_not_event_clocks_control_availability():
    capture,logs,_=fixture()
    with pytest.raises(xray.TraceUnavailable,match="had not arrived"):
        normalize(capture,logs,END+timedelta(seconds=125))
    logs[0]["collected_at"]=timestamp(ASOF+timedelta(seconds=1))
    with pytest.raises(xray.TraceUnavailable,match="had not arrived"):normalize(capture,logs)


def test_other_account_and_noncanonical_queries_are_refused():
    capture,logs,_=fixture()
    capture["context"]["account_id"]="999999999999"
    with pytest.raises(ValueError,match="account/region"):normalize(capture,logs)
    capture,logs,_=fixture()
    capture["pages"][0]["request"]["TraceIds"]=[TRACE]
    with pytest.raises(ValueError,match="query/receipt"):normalize(capture,logs)


def test_cross_host_enclosure_is_not_asserted_without_clock_evidence():
    capture,logs,_=fixture()
    base=Decimal(cw._ms(END-timedelta(minutes=1)))/1000
    edit(capture,1,lambda d:d.update(start_time=base-10,end_time=base+5))
    result=normalize(capture,logs)
    assert result["clock_uncertainty_ns"] is None and not result["actions_allowed"]


def test_unmapped_database_subsegment_retains_explicit_missing_role():
    capture,logs,_=fixture(direct=True)
    base=Decimal(cw._ms(END-timedelta(minutes=1)))/1000
    edit(capture,0,lambda d:d.update(subsegments=[{"id":DB,"name":"unknown-database","namespace":"remote",
        "start_time":base-Decimal(".1"),"end_time":base,"sql":{"database_type":"PostgreSQL","url":"private-db-secret"}}]))
    result=normalize(capture,logs)
    assert result["ignored_spans"]==[{"span_id":DB,"reason":"no-supported-log-attested-business-role"}]
    assert "private-db-secret" not in json.dumps(result)


def test_pagination_empty_pages_budget_and_sdk_error_redaction(monkeypatch):
    original,logs,_=fixture()
    body=original["pages"][0]["response"]
    capture,_,client=fixture(pages=[{"Traces":[],"NextToken":"more"},body])
    assert normalize(capture,logs)["structural_request_validated"] and client.calls[1]["NextToken"]=="more"
    monkeypatch.setattr(xray,"MAX_PAGES",2)
    capture,_,_=fixture(pages=[{"Traces":[],"NextToken":"same"},{"Traces":[],"NextToken":"same"}])
    assert capture["source_error"]
    with pytest.raises(xray.TraceUnavailable):normalize(capture,logs)
    class Failed(Client):
        def batch_get_traces(self,**kwargs):raise RuntimeError("not-a-real-credential-secret")
    capture=xray.XRaySource(Failed([]),region="ap-southeast-1",account_id="123456789012",environment="aws-fixture",
        clock=lambda:ASOF).capture(TRACE,request_entry_service="checkout-service")
    assert capture["source_error"]=={"error_type":"RuntimeError"} and "credential-secret" not in json.dumps(capture)


def test_region_identity_and_original_entry_are_required_before_cloud_access():
    client=Client([]);client.meta=SimpleNamespace(region_name="us-east-1")
    with pytest.raises(ValueError,match="region differs"):
        xray.XRaySource(client,region="ap-southeast-1",account_id="123456789012",environment="aws-fixture")
    assert not client.calls
    assert xray.trace_ids(TRACE)==(XRAY,TRACE)
    for value in ("0"*32,"1-"+"0"*8+"-"+"0"*24,"invalid"):
        with pytest.raises(ValueError):xray.trace_ids(value)


def test_offline_replay_archives_original_inputs_and_refuses_overwrite(tmp_path):
    capture,logs,_=fixture()
    source=ArtifactStore(tmp_path/"source");ref=source.write("raw",capture)
    refs=[source.write("raw",value) for value in logs]
    cmd=[sys.executable,"-m","scripts.replay_xray","--store",str(source.root),"--capture-ref",ref,
        "--environment","aws-fixture","--correlation-id",CORRELATION,"--as-of",timestamp(ASOF),"--output",str(tmp_path/"output")]
    for value in refs:cmd+=["--completion-capture-ref",value]
    run=subprocess.run(cmd,cwd=Path(__file__).parents[1],capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stderr
    result=json.loads(run.stdout);report=ArtifactStore(tmp_path/"output").read(result["report_ref"])
    assert report["structural_request_validated"] and not report["actions_allowed"]
    for value in report["source_refs"]:assert ArtifactStore(tmp_path/"output").read(value)==source.read(value)
    again=subprocess.run(cmd,cwd=Path(__file__).parents[1],capture_output=True,text=True,timeout=30)
    assert again.returncode!=0


def test_observed_http_fault_flag_is_preserved_when_native_and_log_status_agree():
    capture,logs,_=fixture()
    for row in logs[0]["logs"][0]["response"]["events"]:
        body=json.loads(row["message"])
        if body.get("span_id")==ROOT:body["status_code"]=500;row["message"]=json.dumps(body)
    logs[0]["metrics"]["response"]["MetricDataResults"][1]["Values"]=[2]
    def fault(doc):
        doc["fault"]=True;doc["http"]["response"]["status"]=500
    edit(capture,0,fault)
    result=normalize(capture,logs)
    root=next(s for s in result["trace_payload"]["data"][0]["spans"] if s["spanID"]==ROOT)
    assert {tag["key"]:tag["value"] for tag in root["tags"]}["error"] is True
    assert not result["actions_allowed"]


def test_separate_pages_reassemble_disjoint_documents_without_invented_nodes():
    original,logs,_=fixture();body=original["pages"][0]["response"]
    first,second=deepcopy(body),deepcopy(body)
    first["Traces"][0]["Segments"]=first["Traces"][0]["Segments"][:1];first["NextToken"]="rest"
    second["Traces"][0]["Segments"]=second["Traces"][0]["Segments"][1:]
    capture,_,_=fixture(pages=[first,second])
    assert normalize(capture,logs)["trace_payload"]==normalize(original,logs)["trace_payload"]


@pytest.mark.parametrize("problem",["duplicate-key","overlarge-doc","orphan-independent","client-clock","wrong-native-trace-format"])
def test_corrupt_native_documents_fail_without_metadata_or_time_repair(problem):
    capture,logs,_=fixture(independent=True)
    segments=capture["pages"][0]["response"]["Traces"][0]["Segments"]
    if problem=="duplicate-key":segments[0]["Document"]=segments[0]["Document"][:-1]+',"id":"'+ROOT+'"}'
    elif problem=="overlarge-doc":segments[0]["Document"]=" "*(xray.MAX_DOCUMENT_BYTES+1)
    elif problem=="orphan-independent":edit(capture,3,lambda d:d.update(parent_id="a"*16))
    elif problem=="wrong-native-trace-format":edit(capture,0,lambda d:d.update(trace_id=TRACE))
    else:
        base=Decimal(cw._ms(END-timedelta(minutes=1)))/1000
        edit(capture,3,lambda d:d.update(start_time=base-3))
    with pytest.raises(ValueError):normalize(capture,logs)


@pytest.mark.parametrize("problem",["missing-payment","wrong-correlation","inconsistent-counts"])
def test_original_completion_lineage_is_required_for_every_native_business_server(problem):
    capture,logs,_=fixture()
    if problem=="missing-payment":logs=[logs[0],logs[2]]
    elif problem=="inconsistent-counts":logs[1]["metrics"]["response"]["MetricDataResults"][0]["Values"]=[39]
    else:
        row=logs[1]["logs"][0]["response"]["events"][1]
        body=json.loads(row["message"]);body["correlation_id"]="foreign-correlation";row["message"]=json.dumps(body)
    with pytest.raises(ValueError):normalize(capture,logs)


def test_span_depth_and_capture_byte_bounds_fail_closed(monkeypatch):
    capture,logs,_=fixture()
    monkeypatch.setattr(xray,"MAX_SPANS",2)
    with pytest.raises(ValueError,match="budget"):normalize(capture,logs)
    monkeypatch.setattr(xray,"MAX_SPANS",4096)
    monkeypatch.setattr(xray,"MAX_DEPTH",0)
    with pytest.raises(ValueError,match="budget"):normalize(capture,logs)
    monkeypatch.setattr(xray,"MAX_DEPTH",64)
    monkeypatch.setattr(xray,"MAX_BYTES",10)
    capture,_,_=fixture()
    assert capture["source_error"] and capture["pages"]==[]


def test_missing_entry_scope_and_zero_segment_ids_never_reach_supported_shape():
    capture,logs,client=fixture()
    capture.pop("request_entry_service")
    with pytest.raises(ValueError):normalize(capture,logs)
    capture,logs,_=fixture();edit(capture,0,lambda d:d.update(id="0"*16))
    with pytest.raises(ValueError):normalize(capture,logs)


def test_direct_request_cannot_hide_a_captured_unmapped_upstream_parent():
    capture,logs,_=fixture(direct=True)
    native=documents(direct=True)[0]
    parent={"id":"a"*16,"trace_id":XRAY,"name":"unmapped-upstream",
            "start_time":native["start_time"]-1,"end_time":native["end_time"]+1}
    edit(capture,0,lambda d:d.update(parent_id=parent["id"]))
    capture["pages"][0]["response"]["Traces"][0]["Segments"].append({"Id":parent["id"],"Document":native_json(parent)})
    with pytest.raises(xray.TraceUnavailable,match="captured upstream parent"):normalize(capture,logs)
