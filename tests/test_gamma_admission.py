import hashlib
import json
from pathlib import Path

import httpx
import pytest

from sentinelops.public_data.admission import admit_case, audit_cohort_admission, parse_pod, phase_targets, session_groups
from sentinelops.public_data.cohort import download_cohort
from sentinelops.public_data.gamma import METRICS
from test_gamma_archive import range_transport, zip_blob
from test_gamma_cohort import prepare, source
from test_gamma_downloads import EXPERIMENT


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "configs/gamma_admission_profile.json"
EPOCH = 1692000000


def profile():
    return json.loads(PROFILE.read_text())


def pod(entity="service", node="userv2", *, status="Running", namespace="social-network"):
    return (f"Name: service-pod\nNamespace: {namespace}\nNode: {node}/127.0.0.1\n"
            "Start Time: Mon, 14 Aug 2023 00:00:00 +0000\n"
            f"Labels: app-name={entity}\n        pod-template-hash=12345\nAnnotations: <none>\nStatus: {status}\n")


def phase(kind="cpu", begin=EPOCH + 500, end=EPOCH + 700):
    return (f"Bottleneck of type {kind} with measure 0.75 starts at {begin}\n"
            f"Bottleneck of type {kind.upper()} with measure 0.75 ends at {end}\n").encode()


def case_content():
    prefix = f"raw_dataset/{EXPERIMENT}/"
    result = {prefix + "args.txt": json.dumps({"bottlenecked_nodes": ["userv2"], "warm_up_duration": 300}).encode(),
              prefix + "userv2_phases": phase(), prefix + "pods_describe/untrusted-filename.log": pod().encode()}
    for metric in METRICS:
        result[prefix + "prom_metrics/service_" + metric] = json.dumps(
            [[EPOCH + offset, str(100 + offset)] for offset in range(0, 1201, 15)]).encode()
    return result


def test_native_identity_comes_from_labels_and_node_not_pod_or_file_name():
    parsed = parse_pod(pod(), profile())
    assert parsed["entity"] == "service" and parsed["node"] == "userv2"
    content = case_content()
    result = admit_case(EXPERIMENT, content, profile())
    assert result["status"] == "native-evidence-admitted"
    assert result["entities"]["service"]["node"] == "userv2"
    assert result["entities"]["service"]["pod_refs"][0].endswith("untrusted-filename.log")
    assert len(result["events"]) == 1 and result["training_ready"] is False


def test_excluded_monitoring_node_does_not_quarantine_other_native_services():
    content = case_content()
    prefix = f"raw_dataset/{EXPERIMENT}/"
    content[prefix + "pods_describe/jaeger.log"] = pod(entity="jaeger", node="userv17-jaeger").encode()
    for metric in METRICS:
        content[prefix + "prom_metrics/jaeger_" + metric] = content[prefix + "prom_metrics/service_" + metric]
    result = admit_case(EXPERIMENT, content, profile())
    assert result["status"] == "native-evidence-admitted" and set(result["entities"]) == {"service"}
    assert result["entity_exclusions"]["jaeger"] == ["excluded-monitoring-entity"]
    with pytest.raises(ValueError, match="VM lists"):
        phase_targets({"bottlenecked_nodes": ["userv17-jaeger"]}, profile())


@pytest.mark.parametrize("text", [pod(namespace="other"), pod().replace("app-name=service", "app=service"),
    pod().replace("userv2/127.0.0.1", "<none>"), pod() + pod(), pod().replace("+0000", "")])
def test_ambiguous_or_incomplete_native_pods_are_not_guessed(text):
    with pytest.raises(ValueError):
        parse_pod(text, profile())


def test_mixed_target_layout_uses_per_kind_lists_not_global_default():
    args = {"bottleneck_type": "cpu", "cpu_bottlenecked_nodes": ["userv2"],
            "mem_bottlenecked_nodes": ["userv2"], "net_bottlenecked_nodes": None}
    assert phase_targets(args, profile()) == {"cpu_userv2_phases": ("userv2", "cpu"),
                                             "memory_userv2_phases": ("userv2", "memory")}


@pytest.mark.parametrize("change", ["missing-phase", "kind-mismatch", "wrong-clock", "bad-args-target", "nonfinite-warmup", "declared-kind"])
def test_invalid_native_truth_quarantines_case_and_emits_no_partial_events(change):
    content = case_content()
    prefix = f"raw_dataset/{EXPERIMENT}/"
    if change == "missing-phase":
        content.pop(prefix + "userv2_phases")
    elif change == "kind-mismatch":
        content[prefix + "userv2_phases"] = phase("memory").replace(b"type MEMORY", b"type CPU")
    elif change == "wrong-clock":
        content[prefix + "userv2_phases"] = phase(begin=EPOCH + 86400, end=EPOCH + 86500)
    elif change == "bad-args-target":
        content[prefix + "args.txt"] = b'{"bottlenecked_nodes":["userv99"],"warm_up_duration":300}'
    elif change == "nonfinite-warmup":
        content[prefix + "args.txt"] = b'{"bottlenecked_nodes":["userv2"],"warm_up_duration":NaN}'
    else:
        content[prefix + "args.txt"] = b'{"bottlenecked_nodes":["userv2"],"bottleneck_type":"memory","warm_up_duration":300}'
    result = admit_case(EXPERIMENT, content, profile())
    assert result["status"] == "quarantined" and result["events"] == []
    assert any(reason.startswith("native-phase-admission:") for reason in result["reasons"])
    json.dumps(result, allow_nan=False)


def test_conflicting_pods_or_missing_metrics_do_not_create_an_entity_vector():
    content = case_content()
    prefix = f"raw_dataset/{EXPERIMENT}/"
    content[prefix + "pods_describe/second.log"] = pod(node="userv3").encode()
    result = admit_case(EXPERIMENT, content, profile())
    assert result["entities"] == {} and "ambiguous-native-hosting-node" in result["entity_exclusions"]["service"]
    content.pop(prefix + "pods_describe/second.log")
    content[prefix + "prom_metrics/service_" + METRICS[0]] = b"[]"
    result = admit_case(EXPERIMENT, content, profile())
    assert result["entities"] == {} and result["status"] == "quarantined"


def test_simultaneous_cpu_memory_records_remain_separate_native_events():
    content = case_content()
    prefix = f"raw_dataset/{EXPERIMENT}/"
    content[prefix + "args.txt"] = b'{"cpu_bottlenecked_nodes":["userv2"],"mem_bottlenecked_nodes":["userv2"],"warm_up_duration":120}'
    content[prefix + "cpu_userv2_phases"] = content.pop(prefix + "userv2_phases")
    content[prefix + "memory_userv2_phases"] = phase("memory", EPOCH + 510, EPOCH + 710)
    result = admit_case(EXPERIMENT, content, profile())
    assert result["status"] == "native-evidence-admitted" and len(result["events"]) == 2
    assert {event["kind"] for event in result["events"]} == {"cpu", "memory"}
    assert result["native_warmup_seconds"] == 120 and result["training_ready"] is False


def test_network_filename_cannot_replace_mismatched_memory_logs():
    content = case_content()
    prefix = f"raw_dataset/{EXPERIMENT}/"
    content[prefix + "args.txt"] = b'{"net_bottlenecked_nodes":["userv2"],"mem_bottlenecked_nodes":null,"warm_up_duration":120}'
    content[prefix + "memory_userv2_phases"] = content.pop(prefix + "userv2_phases")
    result = admit_case(EXPERIMENT, content, profile())
    assert result["status"] == "quarantined" and result["native_warmup_seconds"] == 120
    assert result["events"] == []


def group_case(name, first, last):
    return {"experiment": name, "resource_clock": None if first is None else {"first": first, "last": last},
            "status": "native-evidence-admitted"}


def test_groups_merge_same_day_and_replicate_family_and_propagate_seen_exclusions():
    day = (EPOCH // 86400) * 86400
    cases = [group_case("a_0", day + 1000, day + 2200), group_case("b_0", day + 5000, day + 6200),
             group_case("b_1", day + 86400 + 1000, day + 86400 + 2200),
             group_case("c_0", day + 86400 * 4 + 1000, day + 86400 * 4 + 2200),
             group_case("unknown_0", None, None)]
    groups = session_groups(cases, {"a_0"}, profile())
    assert groups == session_groups(list(reversed(cases)), {"a_0"}, profile())
    merged = next(item for item in groups if "a_0" in item["experiments"])
    assert merged["experiments"] == ["a_0", "b_0", "b_1"] and merged["final_holdout_eligible"] is False
    assert next(item for item in groups if item["experiments"] == ["c_0"])["final_holdout_eligible"] is True
    assert next(item for item in groups if item["experiments"] == ["unknown_0"])["final_holdout_eligible"] is False


def test_purge_across_midnight_prevents_adjacent_captures_crossing_groups():
    day = (EPOCH // 86400) * 86400
    cases = [group_case("a_0", day + 86000, day + 86300), group_case("b_0", day + 86500, day + 87000)]
    assert len(session_groups(cases, set(), profile())) == 1
    with pytest.raises(ValueError, match="unknown"):
        session_groups(cases, {"not_in_source"}, profile())


def test_fully_quarantined_groups_are_not_counted_as_holdout_candidates():
    case = group_case("case_0", EPOCH, EPOCH + 1200)
    case["status"] = "quarantined"
    assert session_groups([case], set(), profile())[0]["final_holdout_eligible"] is False


def test_full_verified_manifest_join_accounts_for_incomplete_case_and_exclusive_output(tmp_path):
    files = source()
    prefix = f"raw_dataset/{EXPERIMENT}/"
    files.update(case_content())
    files.pop(prefix + "node_phases")
    files.pop(prefix + "pods_describe/service-pod.log")
    transport, _ = range_transport(zip_blob(files))
    with httpx.Client(transport=transport) as client:
        _, scope, lock = prepare(client)
        result = download_cohort(client, tmp_path / "raw", scope, lock)
    config_paths = []
    inspections = {"record_version": "gamma-development-inspections/v1",
        "source_scope_sha256": result["scope_sha256"], "holdout_excluded_experiments": [EXPERIMENT],
        "group_policy": "exclude-entire-connected-session-group-from-final-holdout"}
    for name, value in (("scope", scope), ("tree", result["file_tree_lock"]), ("profile", profile()), ("inspections", inspections)):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        config_paths.append(path)
    output = tmp_path / "admission"
    report = audit_cohort_admission(Path(result["manifest_path"]), *config_paths, output)
    assert report["case_count"] == 2 and report["status_counts"] == {"native-evidence-admitted": 1, "quarantined": 1}
    blob = Path(report["report_path"]).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == report["report_sha256"]
    full = json.loads(blob)
    assert len(full["cases"]) == 2 and full["training_ready"] is False
    assert all(not item["final_holdout_eligible"] for item in full["groups"])
    with pytest.raises(FileExistsError):
        audit_cohort_admission(Path(result["manifest_path"]), *config_paths, output)
