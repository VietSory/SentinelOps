"""Frozen group-first source research dataset; never implicit live HTTP/SLO truth."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

from sentinelops.contracts import validate_payload
from sentinelops.public_data.cohort import load_cohort_manifest, read_bundle
from sentinelops.public_data.gamma import METRICS, canonical_bytes
from sentinelops.public_data.phases import LABEL_VERSION, classify_window
from sentinelops.public_data.resources import iso, resource_window, validate_profile, validate_series, window_cutoffs


def split_assignments(config: dict, groups_config: dict) -> dict[str, dict]:
    fixed = {"split_version": "gamma-grouped-resource/v1", "algorithm": "sha256-test-case-support-calibration/v1",
             "mode": "offline-grouped-source-benchmark", "seed": 42, "test_groups": 2, "calibration_groups": 2,
             "development_anchor_experiment": "cpu_aug12_25min_200_0",
             "calibration_selection": "largest-admitted-case-support-among-remaining-uninspected-groups; sha256-seed-tiebreak"}
    if any(config.get(key) != value for key, value in fixed.items()) or any(
        config.get(key) is not False for key in ("training_ready", "live_compatible", "risk_target_available", "activation_allowed")):
        raise ValueError("split policy differs from the predeclared offline source protocol")
    if (groups_config.get("group_version") != "gamma-source-groups/v1" or groups_config.get("constraints_only") is not True
        or any(config.get(key) != groups_config.get(key) for key in
               ("source_file_tree_sha256", "admission_profile_sha256", "inspections_sha256"))):
        raise ValueError("split config differs from the frozen source-group identities")
    groups = groups_config["groups"]
    if len({group["group_id"] for group in groups}) != len(groups):
        raise ValueError("duplicate source-group identities")
    rank = lambda group: hashlib.sha256(canonical_bytes({"seed": 42, "group_id": group["group_id"]})).hexdigest()
    candidates = sorted([group for group in groups if group["final_holdout_eligible"]], key=rank)
    if len(candidates) < 4:
        raise ValueError("insufficient independent candidate groups for frozen test/calibration roles")
    test = {group["group_id"] for group in candidates[:2]}
    calibration = {group["group_id"] for group in sorted(candidates[2:], key=lambda group: (-group["admitted_cases"], rank(group)))[:2]}
    expected = []
    anchor_found = False
    for group in groups:
        if group["group_id"] in test:
            if group["development_inspected"] or group["unknown_clock_cases"]:
                raise ValueError("development-inspected/unknown source group cannot enter final test")
            split = "test"
        elif group["group_id"] in calibration:
            split = "calibration"
        elif group["admitted_cases"] == 0:
            split = "quarantine"
        elif config["development_anchor_experiment"] in group["experiments"]:
            split = "development"
            anchor_found = True
        else:
            split = "train"
        expected.append({"group_id": group["group_id"], "split": split, "experiments": group["experiments"]})
    if not anchor_found or config.get("assignments") != expected:
        raise ValueError("split assignments changed, overlap, or differ from frozen group-first selection")
    roles = {}
    for item in expected:
        for experiment in item["experiments"]:
            if experiment in roles:
                raise ValueError("source experiment crosses group/split boundaries")
            roles[experiment] = {"split": item["split"], "group_id": item["group_id"]}
    return roles


def build_grouped_dataset(manifest_path: Path, admission_path: Path, scope_path: Path, tree_path: Path,
                          groups_path: Path, splits_path: Path, profile_path: Path, output: Path,
                          *, project: Path | None = None, maximum_windows: int = 1_000_000) -> dict:
    commit = None
    if project is not None:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=project, text=True).strip():
            raise ValueError("commit split assignments and dataset implementation before windowing")
    scope, tree, groups, splits, profile = [json.loads(path.read_text()) for path in
                                          (scope_path, tree_path, groups_path, splits_path, profile_path)]
    validate_profile(profile)
    if splits.get("source_groups_sha256") != hashlib.sha256(groups_path.read_bytes()).hexdigest():
        raise ValueError("source-group snapshot differs from the frozen split hash")
    roles = split_assignments(splits, groups)  # Must succeed before reading/generating any feature window.
    if set(roles) != set(scope["experiments"]):
        raise ValueError("split assignments do not cover every source experiment")
    admission_blob = admission_path.read_bytes()
    if admission_path.stem != hashlib.sha256(admission_blob).hexdigest():
        raise ValueError("native admission report checksum mismatch")
    admission = json.loads(admission_blob)
    if (admission.get("report_version") != "gamma-native-admission/v1"
        or admission.get("source_file_tree_sha256") != tree["source_file_tree_sha256"]
        or admission.get("profile_sha256") != groups["admission_profile_sha256"]
        or admission.get("inspections_sha256") != groups["inspections_sha256"]
        or admission.get("groups") != groups["groups"]
        or admission.get("activation_allowed") is not False):
        raise ValueError("native admission differs from the frozen source/split constraints")
    native_values = {key: admission[key] for key in ("cases", "groups")}
    if hashlib.sha256(canonical_bytes(native_values)).hexdigest() != splits.get("native_evidence_sha256"):
        raise ValueError("native case/event values differ from the frozen split evidence")
    cases = {case["experiment"]: case for case in admission["cases"]}
    if len(cases) != len(admission["cases"]) or set(cases) != set(roles):
        raise ValueError("native admission omits/duplicates source cases")
    if any(case["native_warmup_seconds"] > profile["warmup_seconds"] for case in cases.values()
           if case["status"] == "native-evidence-admitted"):
        raise ValueError("feature profile does not cover declared native warmup")
    manifest, bundles = load_cohort_manifest(manifest_path, scope, expected_tree=tree)
    if (admission.get("scope_sha256") != manifest["scope_sha256"]
        or admission.get("training_data_policy_hash") != manifest["training_data_policy_hash"]):
        raise ValueError("native admission source scope/policy mismatch")
    profile_sha = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    split_sha = hashlib.sha256(splits_path.read_bytes()).hexdigest()
    output.mkdir(parents=True, exist_ok=False)
    counts, labels, eligible_labels, per_entity, case_counts = Counter(), Counter(), Counter(), Counter(), Counter()
    frame_hash, label_hash, index_hash, values_hash = [hashlib.sha256() for _ in range(4)]
    values_hash.update(canonical_bytes({"profile_sha256": profile_sha}))
    rows = 0
    with (output / "resource-windows.jsonl").open("xb") as frames, (output / "interference-labels.jsonl").open("xb") as truths, (output / "window-index.jsonl").open("xb") as index:
        for case_index, experiment in enumerate(scope["experiments"]):
            if case_index % 20 == 0:
                print(f"GAMMA DATASET cases={case_index}/{len(scope['experiments'])} candidate_windows={rows}", flush=True)
            case, role = cases[experiment], roles[experiment]
            split = role["split"]
            case_counts[split + "/" + case["status"]] += 1
            if case["status"] != "native-evidence-admitted":
                continue
            if split == "quarantine":
                raise ValueError("admitted case assigned to quarantine-only group")
            bundle = bundles[experiment]
            if case.get("source_bundle_sha256") != bundle["sha256"]:
                raise ValueError("native case refers to a different raw shard")
            content = read_bundle(bundle)
            for entity, native in sorted(case["entities"].items()):
                prefix = f"raw_dataset/{experiment}/prom_metrics/{entity}_"
                series = {metric: validate_series(json.loads(content[prefix + metric])) for metric in METRICS}
                for end in window_cutoffs(series, profile):
                    rows += 1
                    if rows > maximum_windows:
                        raise ValueError("dataset exceeds its bounded candidate-window budget")
                    result = resource_window(series, end, profile)
                    latest = result.pop("max_input_time")
                    frame = {"schema_version": "1.0", "feature_set_version": profile["feature_set_version"],
                        "source_profile": profile["profile_version"], "mode": profile["mode"], "experiment": experiment,
                        "entity": entity, "window_start": iso(end - 300), "window_end": iso(end),
                        "max_input_observed_at": iso(latest) if latest is not None else None,
                        "source_manifest_sha256": manifest_path.stem, "profile_sha256": profile_sha,
                        "activation_allowed": False, "arrival_times_verified": False, **result}
                    validate_payload("research-resource-window.schema.json", frame)
                    frame_blob = canonical_bytes(frame)
                    row_sha = hashlib.sha256(frame_blob).hexdigest()
                    truth = {"schema_version": "1.0", "label_version": LABEL_VERSION,
                        **{key: frame[key] for key in ("experiment", "entity", "window_start", "window_end")},
                        "hosting_node": native["node"], "resource_window_sha256": row_sha,
                        "source_manifest_sha256": manifest_path.stem, "boundary_guard_seconds": 1,
                        "eligible_for_features": frame["eligible_for_features"], "activation_allowed": False,
                        "precursor_target_available": False, **classify_window(case["events"], native["node"], end - 300, end)}
                    validate_payload("research-interference-label.schema.json", truth)
                    record = {"experiment": experiment, "entity": entity, "window_end": frame["window_end"],
                              "resource_window_sha256": row_sha, **role}
                    for stream, digest, blob in ((frames, frame_hash, frame_blob), (truths, label_hash, canonical_bytes(truth)),
                                                 (index, index_hash, canonical_bytes(record))):
                        stream.write(blob + b"\n"); digest.update(blob + b"\n")
                    numeric = {key: value for key, value in frame.items() if key != "source_manifest_sha256"}
                    values_hash.update(canonical_bytes(numeric) + b"\n")
                    counts[split + "/candidate"] += 1
                    labels[split + "/" + truth["label"]] += 1
                    if frame["eligible_for_features"]:
                        counts[split + "/feature-eligible"] += 1
                        eligible_labels[split + "/" + truth["label"]] += 1
                        per_entity[split + "/" + entity + "/" + truth["label"]] += 1
    report = {"dataset_version": "gamma-grouped-resource/v1", "mode": splits["mode"], "source_commit": commit,
        "source_manifest_sha256": manifest_path.stem, "source_file_tree_sha256": tree["source_file_tree_sha256"],
        "native_admission_sha256": admission_path.stem, "profile_sha256": profile_sha, "split_sha256": split_sha,
        "training_data_policy_hash": manifest["training_data_policy_hash"], "feature_set_version": profile["feature_set_version"],
        "rows": rows, "counts": dict(counts), "case_counts": dict(case_counts), "label_counts": dict(labels),
        "feature_eligible_label_counts": dict(eligible_labels), "per_entity_feature_eligible_labels": dict(per_entity),
        "frame_sha256": frame_hash.hexdigest(), "label_sha256": label_hash.hexdigest(),
        "index_sha256": index_hash.hexdigest(), "feature_values_sha256": values_hash.hexdigest(),
        "source_reference_is_healthy_certificate": False, "source_reference_traffic_verified": False,
        "health_labels_available": False, "training_ready": False,
        "risk_target_available": False, "live_compatible": False, "activation_allowed": False,
        "blocking_reasons": ["recorded interference/reference is a research proxy, not healthy/SLO/precursor truth",
            "model-specific independent reference support and evaluation protocol still require acceptance",
            "source resource features are not verified as the live five HTTP RED features"]}
    blob = canonical_bytes(report)
    digest = hashlib.sha256(blob).hexdigest()
    (output / (digest + ".json")).write_bytes(blob)
    return report | {"report_sha256": digest, "report_path": str(output / (digest + ".json"))}
