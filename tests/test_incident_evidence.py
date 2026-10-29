"""Source-selection software tests; live owned Docker evidence is separate."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json

import httpx
import pytest

from sentinelops.incidents.correlation import correlate_health
from sentinelops.incidents.rca_source import verify_scope
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.workers.incident_evidence import (
    NativeIncidentEvidence, completion_records, select_traces, local_url,
)
from sentinelops.workers.incident_rca import derive_continuous
from test_rca_replay import source_scope, END, POLICY, TRACE
from test_slo_health import POLICY as HEALTH_POLICY


def completion(service="payment-service", **changes):
    method, route = BUSINESS_ROUTES[service]
    row = {"event": "request_completed", "service": service, "method": method, "path": route, "route": route,
        "trace_id": TRACE, "span_id": "1234567890abcdef", "correlation_id": "software-fixture",
        "request_instance_id": "request-fixture-1", "timestamp_unix_ms": int(END.timestamp()) * 1000,
        "duration_ms": 900., "status_code": 200}
    row.update(changes)
    return row


@pytest.mark.parametrize("problem", ["control", "unknown", "service", "trace", "span", "future", "duration", "status"])
def test_unsafe_completion_quarantines_whole_service_instead_of_optimistic_valid_subset(problem):
    first = completion()
    bad = completion(request_instance_id="request-fixture-2")
    if problem == "control": bad["root_cause_ground_truth"] = "injected-label"
    elif problem == "unknown": bad["authorization"] = "synthetic-dummy-not-a-secret"
    elif problem == "service": bad["service"] = "inventory-service"
    elif problem == "trace": bad["trace_id"] = "0" * 32
    elif problem == "span": bad["span_id"] = "non-hex"
    elif problem == "future": bad["timestamp_unix_ms"] += 200000
    elif problem == "duration": bad["duration_ms"] = True
    else: bad["status_code"] = 999
    body = b"\n".join(json.dumps(row).encode() for row in (first, bad))
    records, reason = completion_records(body, "payment-service", END-timedelta(seconds=1),
                                        END+timedelta(seconds=1), END+timedelta(seconds=2))
    assert records == [] and reason["quarantined-service-log-capture"] == 1
    assert len(reason["body_sha256"]) == 64 and "authorization" not in json.dumps(reason)


def test_nonbusiness_controls_are_excluded_and_exact_completion_window_is_preserved():
    row = completion()
    noise = {"event": "fault_configured", "fault_mode": "software-fixture"}
    outside = completion(timestamp_unix_ms=row["timestamp_unix_ms"] + 1)
    body = b"access-log\n" + b"\n".join(json.dumps(r).encode() for r in (noise, row, outside))
    records, reason = completion_records(body, row["service"], END, END, END+timedelta(seconds=2))
    assert records == [{k: v for k, v in row.items() if k != "path"}] and reason["non-business-line"] == 2


def test_inventory_concrete_path_is_projected_out_without_rejecting_its_route_template():
    row = completion("inventory-service", path="/v1/inventory/sku-001")
    records, reason = completion_records(json.dumps(row).encode(), "inventory-service", END, END, END+timedelta(seconds=1))
    assert records == [{k: v for k, v in row.items() if k != "path"}]
    assert not reason and records[0]["route"] == "/v1/inventory/{sku}"


def test_trace_selection_preserves_direct_sibling_samples_without_guessing_shared_scope():
    rows = []
    for service in BUSINESS_ROUTES:
        for index in range(5):
            rows.append(completion(service, trace_id=stable_id([service, index])[:32],
                timestamp_unix_ms=int(END.timestamp()) * 1000 + index))
    # Two siblings in one trace without a Checkout completion cannot identify an entry.
    ambiguous = stable_id("ambiguous-fixture")[:32]
    rows.extend([completion("payment-service", trace_id=ambiguous), completion("inventory-service", trace_id=ambiguous)])
    selection = select_traces(rows, 750.)
    assert len(selection) == 9 and all(t != ambiguous for t, _ in selection)
    for service in BUSINESS_ROUTES:
        selected = [t for t, entry in selection if entry == service]
        assert len(selected) == 3
        assert set(selected) == {stable_id([service, index])[:32] for index in (2, 3, 4)}


@pytest.mark.parametrize("url", ["http://example.com:16686", "https://localhost:16686", "http://localhost",
    "http://localhost:16686/api", "http://dummy:dummy@localhost:16686", "http://localhost:16686?token=dummy"])
def test_native_source_rejects_external_or_credential_bearing_endpoints(url):
    with pytest.raises(ValueError): local_url(url)


def test_trace_http_budget_and_truth_rejection_do_not_archive_partial_or_control_data(tmp_path):
    from sentinelops.telemetry.store import ArtifactStore
    store = ArtifactStore(tmp_path)
    async def run():
        async def handle(request):
            if request.url.path.endswith("1" * 32):
                return httpx.Response(200, content=b"x" * (512 * 1024 + 1))
            return httpx.Response(200, json={"result": {"root_cause_ground_truth": "dummy"}})
        source = NativeIncidentEvidence("fixture", transport=httpx.MockTransport(handle))
        try:
            assert await source._trace("1"*32, "payment-service", store) == (None, "unavailable-or-unsupported-bounded-trace")
            assert await source._trace("2"*32, "payment-service", store) == (None, "unavailable-or-unsupported-bounded-trace")
        finally:
            await source.close()
    asyncio.run(run())
    assert list(tmp_path.rglob("*.json")) == []


def continuous_fixture(tmp_path):
    """Pure derivation fixture; does not claim PostgreSQL job authentication."""
    source, health, correlation, ref, _ = source_scope(tmp_path, fresh=True)
    upstream = verify_scope(source, health, correlation, ref)
    identity = {"environment": "local", "fixture": "continuous-derivation-only"}
    scope = {"scope_version": "continuous-episode-scope/v1", "health_stream_id": stable_id(identity)}
    opening_objects = [{"signal_ref": r, "signal": s} for r, s in upstream["signals"].values() if s["event"] == "incident_opened"]
    correlated = correlate_health([o["signal"] for o in opening_objects], upstream["correlation"]["topology_snapshot"],
        as_of=END+timedelta(minutes=3), source_scope_sha256=stable_id(scope))
    for item in opening_objects: source.write("signals", item["signal"])
    closed = {"scope": scope, "scope_ref": source.write("manifests", scope),
              "correlation": correlated, "opening_objects": opening_objects}
    group = correlated["groups"][0]
    intent = {"group_id": group["group_id"], "job_id": stable_id("fixture-job"),
        "detected_at": timestamp(END+timedelta(minutes=4)), "rca_policy": POLICY,
        "correlation_ref": source.write("manifests", correlated), "scope_ref": closed["scope_ref"],
        "opening_objects": opening_objects}
    intent_ref = source.write("manifests", intent)
    receipts = [{"service": s, "container_id": stable_id(s), "compose_project": "fixture", "running": True,
                 "collected_at": timestamp(END+timedelta(seconds=40)), "selection": {}} for s in sorted(BUSINESS_ROUTES)]
    logs = deepcopy(upstream["logs"])
    logs.update(projection_version="business-completion/v1", source_receipts=receipts)
    logs_ref = source.write("raw", logs)
    # Each stored trace is selected from the business completion violations.
    trace = deepcopy(upstream["traces"][0][1]);trace["request_entry_service"] = "checkout-service"
    trace_ref = source.write("raw", trace)
    profile = {"profile_version": "software-fixture/v1", "infrastructure_source": "postgres-stat://fixture:5432"}
    capture = {"capture_version": "continuous-incident-evidence/v1", "job_id": intent["job_id"],
        "intent_ref": intent_ref, "profile": profile, "health_stream_id": stable_id(identity),
        "window_start": group["first_onset_at"], "window_end": timestamp(END+timedelta(minutes=3)),
        "analyzed_at": timestamp(END+timedelta(minutes=5)), "actions_allowed": False,
        "evidence_refs": {"logs": logs_ref, "traces": [trace_ref], "infrastructure": upstream["refs"]["infrastructure"]},
        "missing_counts": {},
        "source_containers": {r["service"]: {k: r[k] for k in ("service", "container_id", "compose_project", "running")} for r in receipts}}
    return source, intent_ref, intent, closed, capture, profile, identity


def derive(fixture):
    store, intent_ref, intent, closed, capture, profile, identity = fixture
    return derive_continuous(store, intent_ref=intent_ref, intent=intent, closed=closed,
        capture_ref=store.write("manifests", capture), profile=profile, health_identity=identity, health_policy=HEALTH_POLICY)


def test_actual_clock_derivation_uses_same_scores_and_keeps_missing_modalities(tmp_path):
    fixture = continuous_fixture(tmp_path)
    row = derive(fixture)
    pay = next(c for c in row["rca"]["candidates"] if c["service"] == "payment-service")
    assert pay["score"] == pytest.approx(.25*(999_000_000/1_200_000_000)+.15)
    assert pay["components"]["anomaly_strength"]["value"] is None
    assert pay["components"]["recent_change_support"]["value"] is None
    assert row["rca"]["abstained"] and row["rca"]["actions_allowed"] is False
    clocks = [f["fact"] for f in row["rca"]["evidence"] if f["type"] == "timeline"]
    assert clocks and all("actual health decision clock" in fact and "replay clock" not in fact for fact in clocks)


def test_optional_model_capture_requires_exact_release_and_preserves_unavailable_inference(tmp_path):
    from sentinelops.ml.registry import ModelRegistry
    fixture = continuous_fixture(tmp_path)
    store, intent_ref, intent, closed, capture, profile, identity = fixture
    registry = ModelRegistry()
    normal_ref = store.write("manifests", {"feature_refs": []})
    batch_ref = store.write("manifests", {"normalization_manifest_ref": normal_ref, "bucket_end": capture["window_end"]})
    state_ref = store.write("manifests", {"last_batch_ref": batch_ref})
    capture["capture_version"] = "continuous-incident-evidence/v2"
    capture["profile"] = {**profile, "model_release_ref": registry.identity}
    capture["model_evidence"] = {"release_ref": registry.identity, "source_state_ref": state_ref,
        "feature_batch_ref": batch_ref, "signals": []}
    kwargs = dict(intent_ref=intent_ref, intent=intent, closed=closed, capture_ref=store.write("manifests", capture),
        profile=capture["profile"], health_identity=identity, health_policy=HEALTH_POLICY)
    with pytest.raises(ValueError, match="exact separately validated"):
        derive_continuous(store, **kwargs)
    row = derive_continuous(store, **kwargs, model_registry=registry)
    assert row["model_signals"] == [] and row["rca"]["abstained"]
    assert all(c["components"]["anomaly_strength"]["value"] is None for c in row["rca"]["candidates"] if c["service"] != "rds")


@pytest.mark.parametrize("problem", ["job", "profile", "cutoff", "log-receipt", "trace-receipt", "trace-selection", "trace-budget", "log-control", "infra-source"])
def test_hash_valid_capture_cannot_escape_accepted_job_receipts_or_source_selection(tmp_path, problem):
    fixture = continuous_fixture(tmp_path)
    store, _, _, _, capture, _, _ = fixture
    if problem == "job": capture["job_id"] = "0" * 64
    elif problem == "profile": capture["profile"] = {"profile_version": "changed"}
    elif problem == "cutoff": capture["analyzed_at"] = timestamp(END)
    elif problem in ("log-receipt", "log-control"):
        logs = store.read(capture["evidence_refs"]["logs"])
        if problem == "log-receipt": logs["source_receipts"][0]["collected_at"] = timestamp(END+timedelta(hours=1))
        else: logs["records"][0]["risk_label"] = "dummy"
        capture["evidence_refs"]["logs"] = store.write("raw", logs)
    elif problem in ("trace-receipt", "trace-selection", "trace-budget"):
        trace = store.read(capture["evidence_refs"]["traces"][0])
        if problem == "trace-receipt": trace["collected_at"] = timestamp(END+timedelta(hours=1))
        elif problem == "trace-selection": trace["trace_id"] = "f" * 32
        else: trace["body"] += " " * (512 * 1024)
        capture["evidence_refs"]["traces"] = [store.write("raw", trace)]
    else:
        point = store.read(capture["evidence_refs"]["infrastructure"][0])
        point["source_ref"] = "postgres-stat://another:5432"
        capture["evidence_refs"]["infrastructure"] = [store.write("observations", point)]
    with pytest.raises(ValueError): derive(fixture)
