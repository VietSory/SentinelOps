from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq

from sentinelops.incidents.native_rca import evaluate_native
from sentinelops.incidents.rca import COMPONENTS, METHODS, _score, unavailable
from sentinelops.public_data.rcaeval_native import adapt_native_case, digest, load_native_inputs
from sentinelops.public_data.rcaeval_partitions import INSPECTED, REVISION, canonical

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile():
    return json.loads((ROOT / "configs/rcaeval_native_profile.json").read_text())


@pytest.fixture
def policy():
    return json.loads((ROOT / "configs/rca_policy.json").read_text())


def parquet(table):
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    return sink.getvalue().to_pybytes()


def metrics(times=(1000, 1001, 1002), values=(None, float("nan"), 2.)):
    return parquet(pa.table({"time": pa.array(times, type=pa.int64()), "frontend_cpu": pa.array(values, type=pa.float64())}))


def logs(messages=("ok", "ValueError: sensitive@example.com"), entities=("frontend", "frontend")):
    return parquet(pa.table({"timestamp": pa.array([1002] * len(messages), type=pa.int64()),
                             "container_name": pa.array(entities, type=pa.large_string()),
                             "message": pa.array(messages, type=pa.large_string())}))


def traces(*, duplicate=False, pair_offset=0, start_shift=0, duration=101):
    starts = [1002000001 + start_shift, 1001000002 + start_shift]
    table = {"time": ["00:16", "00:16"], "traceID": ["00001", "00001"],
             "spanID": ["000a", "000a" if duplicate else "000b"], "serviceName": ["frontendservice", "frontend"],
             "methodName": [None, "call"], "operationName": ["operation", "operation"], "parentSpanID": ["000b", None],
             "startTimeMillis": [s // 1000 + pair_offset for s in starts], "startTime": starts,
             "duration": [duration, duration * 3], "statusCode": [14, None]}
    ints = {"startTimeMillis", "startTime", "duration", "statusCode"}
    return parquet(pa.table({k: pa.array(v, type=pa.int64() if k in ints else pa.large_string()) for k, v in table.items()}))


def test_native_null_nan_exact_clocks_ids_unsorted_and_no_aliases(profile, policy):
    data = {"metrics": metrics(), "logs": logs(), "traces": traces()}
    scope = adapt_native_case(data, profile)
    assert scope == adapt_native_case(data, profile)
    assert scope["entities"] == ["frontend", "frontendservice"]
    channel = scope["modalities"]["metrics"]["channels"]["frontend_cpu"]
    assert (channel["window_null"], channel["window_nan"], channel["window_observed"]) == (1, 1, 1)
    assert channel["minimum"] == channel["maximum"] == channel["last"] == 2.
    assert scope["trace_support"]["frontendservice"]["recorded_duration_ns"] == 101000
    assert scope["modalities"]["traces"]["backwards_adjacent_times"] == 1
    assert scope["modalities"]["traces"]["native_status_counts"] == {"14": 1, "null": 1}
    assert scope["observed_parent_links"] == [{"consumer": "frontend", "dependency": "frontendservice", "spans": 1}]
    assert "sensitive@example.com" not in json.dumps(scope)
    result = evaluate_native(scope, policy)
    assert result["abstained"] and result["qualified_root"] is None
    assert "source-availability-unverified" in result["abstention_reasons"]
    front = next(c for c in result["candidates"] if c["service"] == "frontend")
    assert front["score"] == pytest.approx(.25 * .75 + .15)
    assert front["coverage"] == .4
    assert front["components"]["log_support"]["value"] == 1.
    assert result["actions_allowed"] is result["live_rca_compatible"] is False


def test_missing_modalities_and_model_do_not_become_healthy_or_scores(profile, policy):
    scope = adapt_native_case({"metrics": metrics()}, profile)
    result = evaluate_native(scope, policy)
    assert scope["modalities"]["logs"]["present"] is False
    assert result["diagnostic_ranking"] == []
    assert result["candidates"][0]["score"] is None
    assert result["candidates"][0]["coverage"] == 0
    assert all(c["value"] is None for c in result["candidates"][0]["components"].values())


@pytest.mark.parametrize("messages,expected", [(("ok",), 0.), ((None,), None),
    (("RuntimeException",), 1.), (("Traceback (most recent call last):",), 1.),
    (("panic: failed",), 1.), (("error-rate metric",), 0.)])
def test_fixed_exception_markers_without_severity_or_imputation(profile, policy, messages, expected):
    scope = adapt_native_case({"metrics": metrics(), "logs": logs(messages, ("frontend",))}, profile)
    card = evaluate_native(scope, policy)["candidates"][0]["components"]["log_support"]
    assert card["value"] == expected


@pytest.mark.parametrize("change,match", [("duplicate", "duplicate"), ("pair", "unit pair"),
    ("clock", "outside"), ("negative-duration", "exact nonnegative"), ("overflow", "exact nonnegative")])
def test_malformed_trace_clocks_and_identity_fail_closed(profile, change, match):
    options = {"duplicate": {"duplicate": True}, "pair": {"pair_offset": 1},
               "clock": {"start_shift": 10**12}, "negative-duration": {"duration": -1},
               "overflow": {"duration": 2**63 // 4}}[change]
    with pytest.raises(ValueError, match=match):
        adapt_native_case({"metrics": metrics(), "traces": traces(**options)}, profile)


@pytest.mark.parametrize("data,match", [(lambda: metrics((1001, 1000, 1002)), "increasing"),
    (lambda: metrics(values=(1., float("inf"), 2.)), "infinite"),
    (lambda: parquet(pa.table({"time": [1000., 1001.], "frontend_cpu": [1., 2.]})), "types")])
def test_bad_metric_time_and_values_are_not_filled(profile, data, match):
    with pytest.raises(ValueError, match=match):
        adapt_native_case({"metrics": data()}, profile)


def test_native_profile_and_policy_cannot_be_tuned_during_evaluation(profile, policy):
    modified = deepcopy(profile)
    modified["analysis_window_seconds"] = 60
    with pytest.raises(ValueError, match="frozen"):
        adapt_native_case({"metrics": metrics()}, modified)
    scope = adapt_native_case({"metrics": metrics()}, profile)
    policy["minimum_score"] = .1
    with pytest.raises(ValueError, match="frozen"):
        evaluate_native(scope, policy)


def test_integer_epoch_conversion_above_float_precision_retains_microsecond(profile):
    epoch = 1705355286
    source = pq.read_table(pa.BufferReader(traces())).to_pydict()
    source["startTime"] = [epoch * 1000000 + 1, epoch * 1000000 + 2]
    source["startTimeMillis"] = [epoch * 1000, epoch * 1000]
    data = parquet(pa.table({k: pa.array(v, type=pa.int64() if k in {"startTime", "startTimeMillis", "duration", "statusCode"}
                                         else pa.large_string()) for k, v in source.items()}))
    scope = adapt_native_case({"metrics": metrics((epoch - 2, epoch - 1, epoch)), "traces": data}, profile)
    assert scope["modalities"]["traces"]["source_start_ns"] == 1705355286000001000
    assert scope["modalities"]["traces"]["source_end_ns"] == 1705355286000002000


def test_scope_tampering_and_self_consistent_invalid_counts_fail(profile, policy):
    scope = adapt_native_case({"metrics": metrics(), "logs": logs()}, profile)
    scope["log_support"]["frontend"]["exception_markers"] = 99
    with pytest.raises(ValueError, match="identity"):
        evaluate_native(scope, policy)
    scope["scope_sha256"] = digest({k: v for k, v in scope.items() if k != "scope_sha256"})
    with pytest.raises(ValueError, match="inconsistent"):
        evaluate_native(scope, policy)


def test_even_rehashed_evaluator_metadata_is_rejected_by_business_scope(profile, policy):
    scope = adapt_native_case({"metrics": metrics()}, profile)
    scope["ground_truth_service"] = "frontend"
    scope["scope_sha256"] = digest({k: v for k, v in scope.items() if k != "scope_sha256"})
    with pytest.raises(ValueError, match="boundary"):
        evaluate_native(scope, policy)


def test_native_methods_do_not_enter_live_method_admission(profile, policy):
    scope = adapt_native_case({"metrics": metrics(), "logs": logs()}, profile)
    candidate = evaluate_native(scope, policy)["candidates"][0]
    assert "native-exception-marker-presence/v1" not in METHODS["log_support"]
    facts = {ref: {"type": "log", "service": "frontend"} for ref in candidate["evidence_refs"]}
    with pytest.raises(ValueError, match="method/evidence"):
        _score(candidate["components"], facts, "frontend", policy)


def test_telemetry_scope_rejects_evaluator_fields(profile):
    with pytest.raises(ValueError, match="only explicit telemetry"):
        adapt_native_case({"metrics": metrics(), "inject_time": b"1001"}, profile)


def save_input_manifest(tmp_path, data):
    path = tmp_path / "manifests" / (hashlib.sha256(canonical(data)).hexdigest() + ".json")
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(canonical(data))
    return path


def test_native_loader_verifies_bytes_before_decode_and_retains_verified_buffers(tmp_path, profile, monkeypatch):
    from sentinelops.public_data import rcaeval_native as native
    case = INSPECTED[0]
    path = tmp_path / "rcaeval" / REVISION / case / "metrics.parquet"
    path.parent.mkdir(parents=True)
    original = metrics()
    path.write_bytes(original)
    sha = hashlib.sha256(original).hexdigest()
    manifest = {"manifest_version": "public-download/v1", "dataset": "RCAEval", "revision": REVISION,
                "activation_allowed": False, "selection": [case],
                "files": [{"path": str(path), "sha256": sha, "publisher_checksum": {"size": len(original), "sha256": sha}}]}
    # Synthetic trusted inventory exercises I/O mechanics without claiming publisher bytes.
    monkeypatch.setattr(native, "PINNED_FILES", {case + "/metrics.parquet": {"size": len(original), "sha256": sha}})
    selected = save_input_manifest(tmp_path, manifest)
    _, loaded = load_native_inputs(selected, profile)
    path.write_bytes(b"corrupted after verification")
    assert adapt_native_case(loaded[case], profile)["modalities"]["metrics"]["rows"] == 3
    with pytest.raises(ValueError):
        load_native_inputs(selected, profile)


def test_decoder_rejects_footer_budget_before_batch_allocation(profile, monkeypatch):
    from sentinelops.public_data import rcaeval_native as native
    # Exercise limits with a synthetic protocol identity; production profile stays frozen.
    monkeypatch.setattr(native, "validate_profile", lambda p: None)
    profile["maximum_rows_per_table"] = 1
    with pytest.raises(ValueError, match="budget"):
        adapt_native_case({"metrics": metrics()}, profile)
    profile["maximum_rows_per_table"] = 10
    profile["maximum_decoded_bytes_per_table"] = 1
    with pytest.raises(ValueError, match="budget"):
        adapt_native_case({"metrics": metrics()}, profile)


def test_identity_memory_budgets_are_enforced(profile, monkeypatch):
    from sentinelops.public_data import rcaeval_native as native
    monkeypatch.setattr(native, "validate_profile", lambda p: None)
    profile["maximum_entities"] = 1
    with pytest.raises(ValueError, match="entity budget"):
        adapt_native_case({"metrics": metrics(), "traces": traces()}, profile)
    profile["maximum_entities"] = 10
    profile["maximum_unique_span_ids"] = 1
    with pytest.raises(ValueError, match="span identity budget"):
        adapt_native_case({"metrics": metrics(), "traces": traces()}, profile)


def test_evaluator_reads_labels_only_after_all_business_results_are_frozen(profile, policy, monkeypatch):
    from sentinelops.public_data import rcaeval_evaluation as evaluator
    events = []
    inputs = {case: {"metrics": metrics()} for case in INSPECTED}
    monkeypatch.setattr(evaluator, "load_native_inputs", lambda *a: ({}, inputs))
    original = evaluator.evaluate_native
    def observed(scope, policy):
        events.append("business")
        return original(scope, policy)
    def labels(path):
        assert events == ["business"] * 3
        events.append("labels")
        return [{"case": case, "root_cause_service": "label-only-root", "time_start": 1000,
                 "time_end": 1002, "inject_time": 1001} for case in INSPECTED], {}
    monkeypatch.setattr(evaluator, "evaluate_native", observed)
    monkeypatch.setattr(evaluator, "read_index", labels)
    first = evaluator.evaluate_manifest(Path("a" * 64 + ".json"), profile, policy, source_commit="fixture")
    assert events[-1] == "labels"
    assert first["denominators"] == {"selected": 3, "evaluated": 3, "quarantined": 0, "abstained": 3, "qualified": 0, "qualified_correct": 0}
    assert first["conditional_qualified_accuracy"] is None
    assert "label-only-root" not in json.dumps([c["observations"] for c in first["cases"]])


def test_root_and_injection_label_changes_cannot_change_business_diagnostics(profile, policy, monkeypatch):
    from sentinelops.public_data import rcaeval_evaluation as evaluator
    monkeypatch.setattr(evaluator, "load_native_inputs", lambda *a: ({}, {
        case: {"metrics": metrics(), "logs": logs(), "traces": traces()} for case in INSPECTED}))
    def labels(root, injection):
        return [{"case": case, "root_cause_service": root, "time_start": 1000,
                 "time_end": 1002, "inject_time": injection} for case in INSPECTED], {}
    monkeypatch.setattr(evaluator, "read_index", lambda *a: labels("frontend", 1001))
    first = evaluator.evaluate_manifest(Path("a" * 64 + ".json"), profile, policy, source_commit="fixture")
    monkeypatch.setattr(evaluator, "read_index", lambda *a: labels("missing-root", -100))
    second = evaluator.evaluate_manifest(Path("a" * 64 + ".json"), profile, policy, source_commit="fixture")
    assert first["business_results_sha256"] == second["business_results_sha256"]
    assert all(a["diagnosis"] == b["diagnosis"] and a["observations"] == b["observations"]
               for a, b in zip(first["cases"], second["cases"]))
    assert all(c["label_clock_in_capture"] is False for c in second["cases"])


def test_quarantine_does_not_count_as_diagnostic_execution_or_accuracy(profile, policy, monkeypatch):
    from sentinelops.public_data import rcaeval_evaluation as evaluator
    monkeypatch.setattr(evaluator, "load_native_inputs", lambda *a: ({}, {
        case: {"metrics": metrics(values=(float("inf"), 1., 2.))} for case in INSPECTED}))
    monkeypatch.setattr(evaluator, "read_index", lambda *a: ([
        {"case": case, "root_cause_service": "frontend", "time_start": 1000,
         "time_end": 1002, "inject_time": 1001} for case in INSPECTED], {}))
    report = evaluator.evaluate_manifest(Path("a" * 64 + ".json"), profile, policy, source_commit="fixture")
    assert report["status"] == "INCOMPLETE"
    assert report["denominators"]["evaluated"] == report["denominators"]["qualified"] == 0
    assert report["denominators"]["quarantined"] == 3
    assert report["conditional_qualified_accuracy"] is report["abstention_rate"] is None
    assert all(c["reason"] == "infinite native metric value" for c in report["cases"])


def test_unfinished_spans_are_not_counted_as_available_window_support(profile, policy):
    scope = adapt_native_case({"metrics": metrics(), "traces": traces(duration=2000000)}, profile)
    assert scope["modalities"]["traces"]["rows"] == 2
    assert scope["modalities"]["traces"]["eligible_window_records"] == 0
    assert scope["trace_support"] == {}
    assert evaluate_native(scope, policy)["diagnostic_ranking"] == []


@pytest.mark.parametrize("change", ["checksum-drift", "omitted-modality", "added-file", "publisher-size"])
def test_forged_native_inventory_is_rejected_before_any_cache_byte(tmp_path, profile, monkeypatch, change):
    from sentinelops.public_data import rcaeval_native as native
    case = INSPECTED[1]
    base = tmp_path / "rcaeval" / REVISION
    records = []
    for name, expected in native.PINNED_FILES.items():
        if "/" in name and name.split("/", 1)[0] != case:
            continue
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"must-not-be-read")
        records.append({"path": str(path), "sha256": expected["sha256"],
                        "publisher_checksum": {"size": expected["size"], "sha256": expected["sha256"]}})
    telemetry = next(r for r in records if r["path"].endswith("/metrics.parquet"))
    if change == "checksum-drift":
        telemetry["sha256"] = telemetry["publisher_checksum"]["sha256"] = "0" * 64
    elif change == "publisher-size":
        telemetry["publisher_checksum"]["size"] += 1
    elif change == "omitted-modality":
        records = [r for r in records if not r["path"].endswith("/traces.parquet")]
    else:
        records.append({"path": str(base / case / "root_cause.txt"), "sha256": "0" * 64,
                        "publisher_checksum": {"size": 10, "sha256": "0" * 64}})
    manifest = {"manifest_version": "public-download/v1", "dataset": "RCAEval", "revision": REVISION,
                "activation_allowed": False, "selection": [case], "files": records}
    selected = save_input_manifest(tmp_path, manifest)
    original = native._bounded_bytes
    def only_manifest(path, limit):
        if path != selected:
            pytest.fail("cache byte touched before the entire trusted inventory was admitted")
        return original(path, limit)
    monkeypatch.setattr(native, "_bounded_bytes", only_manifest)
    with pytest.raises(ValueError, match="inventory"):
        load_native_inputs(selected, profile)
