from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
import re
from typing import Any

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.experiments.labels import label_feature
from sentinelops.telemetry.core import BUSINESS_ROUTES, FEATURE_SET, stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore

SPLITS = ("train", "validation", "test")


def _bind_dataset(store: ArtifactStore, identifier: str, reference: str, fingerprint: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", identifier):
        raise ValueError("invalid dataset ID")
    binding = {"dataset_id":identifier,"manifest_ref":reference,"fingerprint":fingerprint}
    blob = store.write("manifests", binding)
    source = store.root / blob.removeprefix("artifact://")
    target = store.root / "dataset-index" / (identifier+".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
        return reference
    except FileExistsError:
        recorded = json.loads(target.read_text())
        if recorded.get("fingerprint") != fingerprint:
            raise ValueError("dataset ID is already frozen with different contents")
        saved = store.read(recorded["manifest_ref"])
        if stable_id({key:value for key,value in saved.items() if key != "created_at"}) != fingerprint:
            raise ValueError("dataset binding is corrupt")
        return recorded["manifest_ref"]


def assemble_dataset(inputs: list[dict[str, Any]], store: ArtifactStore, *, dataset_id: str,
                     allow_pilot: bool = False, now: datetime | None = None) -> dict[str, Any]:
    if not inputs:
        raise ValueError("dataset requires experiment runs")
    run_ids, session_split, profiles, environments = set(), {}, set(), set()
    source_manifests, slo_versions, slo_configs = set(), set(), set()
    ranges = defaultdict(list)
    rows, run_refs, exclusions = [], [], Counter()
    healthy_counts = {split:Counter() for split in SPLITS}
    positive_counts = {split:Counter() for split in SPLITS}
    negative_counts = {split:Counter() for split in SPLITS}
    healthy_runs = {split:Counter() for split in SPLITS}
    risk_runs = {split:set() for split in SPLITS}
    feature_owners = {}
    for item in sorted(inputs, key=lambda value:value["run"]["run_id"]):
        run = item["run"]
        validate_payload("experiment-run.schema.json", run)
        if run["status"] != "completed" or run["dirty_worktree"]:
            raise ValueError("only completed clean-source runs can enter a dataset")
        if run["run_id"] in run_ids:
            raise ValueError("duplicate run ID")
        run_ids.add(run["run_id"])
        if run["source_manifest_ref"] in source_manifests:
            raise ValueError("source capture reused across experiment runs")
        source_manifests.add(run["source_manifest_ref"])
        slo_versions.add(run["slo_config_version"])
        slo_configs.add(stable_id(run["parameters"]["slo"]))
        split = run["declared_split"]
        session = run["session_id"]
        if session in session_split and session_split[session] != split:
            raise ValueError("session leakage across splits")
        session_split[session] = split
        profiles.add(run["source_profile"])
        environments.add(run["environment"])
        ranges[split].append((utc_time(run["started_at"]), utc_time(run["ended_at"])))
        run_refs.append({"run_id":run["run_id"],"session_id":session,"declared_split":split,
                         "truth_ref":item["truth_ref"],"source_manifest_ref":run["source_manifest_ref"]})
        seen = set()
        healthy_services = set()
        for feature in item["features"]:
            if feature["record_id"] in seen:
                continue
            seen.add(feature["record_id"])
            if feature["record_id"] in feature_owners and feature_owners[feature["record_id"]] != run["run_id"]:
                raise ValueError("feature reused across experiment runs")
            feature_owners[feature["record_id"]] = run["run_id"]
            row = label_feature(feature, run)
            if row is None:
                exclusions["warmup_ineligible_or_unknown_truth"] += 1
                continue
            rows.append(row)
            healthy_counts[split][row["service"]] += int(row["is_healthy"])
            positive_counts[split][row["service"]] += int(row["risk_label"] == 1)
            negative_counts[split][row["service"]] += int(row["risk_label"] == 0)
            if row["is_healthy"]:
                healthy_services.add(row["service"])
            if row["risk_label"] == 1:
                risk_runs[split].add(run["run_id"])
        for service in healthy_services:
            healthy_runs[split][service] += 1
    if len(profiles) != 1 or len(environments) != 1 or len(slo_versions) != 1 or len(slo_configs) != 1:
        raise ValueError("dataset cannot mix source profiles, environments or SLO versions")
    ordered_ranges = sorted(interval for intervals in ranges.values() for interval in intervals)
    if any(earlier[1] > later[0] for earlier,later in zip(ordered_ranges,ordered_ranges[1:])):
        raise ValueError("single-testbed experiment runs must not overlap")
    for earlier, later in (("train","validation"),("validation","test"),("train","test")):
        if ranges[earlier] and ranges[later] and max(end for _,end in ranges[earlier]) > min(start for start,_ in ranges[later]):
            raise ValueError("chronological split ordering violated")
    reasons = []
    for split in SPLITS:
        if not ranges[split]:
            reasons.append(f"missing_{split}_runs")
    for service in BUSINESS_ROUTES:
        if healthy_counts["train"][service] < 300:
            reasons.append(f"insufficient_healthy_train:{service}")
        if healthy_counts["validation"][service] < 100:
            reasons.append(f"insufficient_healthy_calibration:{service}")
        if healthy_runs["train"][service] < 3 or healthy_runs["validation"][service] < 2:
            reasons.append(f"insufficient_independent_healthy_runs:{service}")
    for split in ("train","validation"):
        if sum(positive_counts[split].values()) == 0 or sum(negative_counts[split].values()) == 0:
            reasons.append(f"missing_risk_classes:{split}")
        if len(risk_runs[split]) < (3 if split == "train" else 2):
            reasons.append(f"insufficient_gradual_runs:{split}")
    if reasons and not allow_pilot:
        raise ValueError("dataset is not training-ready: "+"; ".join(reasons))
    rows.sort(key=lambda row:(row["declared_split"],row["run_id"],row["service"],row["window_end"]))
    table_ref = store.write("features", {"table_version":"training-table/v1","feature_set_version":FEATURE_SET,"rows":rows})
    created = now or datetime.now(timezone.utc)
    if created < max(end for intervals in ranges.values() for _,end in intervals):
        raise ValueError("dataset cannot be frozen before its runs complete")
    manifest = {
        "schema_version":"1.0","dataset_id":dataset_id,"created_at":timestamp(created),
        "feature_set_version":FEATURE_SET,"source_profile":next(iter(profiles)),"environment":next(iter(environments)),
        "label_protocol_version":"degradation-phase/v1","slo_config_version":next(iter(slo_versions)),
        "slo_config_hash":next(iter(slo_configs)),
        "status":"pilot" if reasons else "training-ready","training_ready":not reasons,
        "blocking_reasons":reasons,"run_refs":run_refs,"feature_rows_ref":table_ref,
        "row_count":len(rows),"excluded_counts":dict(exclusions),
        "healthy_counts":{split:dict(values) for split,values in healthy_counts.items()},
        "risk_positive_counts":{split:dict(values) for split,values in positive_counts.items()},
        "risk_negative_counts":{split:dict(values) for split,values in negative_counts.items()},
        "healthy_run_counts":{split:dict(values) for split,values in healthy_runs.items()},
        "risk_positive_run_counts":{split:len(values) for split,values in risk_runs.items()},
    }
    validate_payload("dataset-manifest.schema.json", manifest)
    reference = store.write("manifests", manifest)
    fingerprint = stable_id({key:value for key,value in manifest.items() if key != "created_at"})
    reference = _bind_dataset(store, dataset_id, reference, fingerprint)
    manifest = store.read(reference)
    manifest["manifest_ref"] = reference
    return manifest
