"""Independent native interference evidence; never a precursor/SLO target."""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

from sentinelops.contracts import validate_payload
from sentinelops.public_data.gamma import canonical_bytes, load_gamma_manifest, phase_file
from sentinelops.public_data.resources import validate_series


LABEL_VERSION = "gamma-recorded-interference/v1"
BOUNDARY_GUARD_SECONDS = 1
LINE = re.compile(r"Bottleneck of type ([A-Za-z][A-Za-z0-9_-]*) with measure "
                  r"([0-9]+(?:\.[0-9]+)?) (starts|ends) at ([0-9]+(?:\.[0-9]+)?)")


def parse_phases(text: str, *, node: str, source_sha256: str) -> list[dict]:
    """Pair the publisher's explicit start/end records, without inferring gaps."""
    active = {}
    previous = -math.inf
    events = []
    lines = text.splitlines()
    if not lines:
        raise ValueError("empty native phase log cannot establish recorded interference coverage")
    for line in lines:
        match = LINE.fullmatch(line)
        if not match:
            raise ValueError("unrecognized native phase log line")
        kind, measure, transition, timestamp = match.groups()
        kind, measure, timestamp = kind.lower(), float(measure), float(timestamp)
        if not math.isfinite(measure) or not math.isfinite(timestamp) or not 1e9 <= timestamp < 1e10:
            raise ValueError("phase measures/times must be finite and time must be Unix seconds")
        if timestamp < previous:
            raise ValueError("native phase log times must not decrease")
        previous = timestamp
        key = kind, measure
        if transition == "starts":
            if key in active:
                raise ValueError("duplicate active phase without an end")
            active[key] = timestamp
        else:
            if key not in active or timestamp <= active[key]:
                raise ValueError("unmatched or nonpositive native phase interval")
            start = active.pop(key)
            event_id = hashlib.sha256(canonical_bytes({"source": source_sha256, "node": node, "ordinal": len(events)})).hexdigest()
            events.append({"event_id": event_id, "node": node,
                           "kind": kind, "measure": measure, "start": start, "end": timestamp,
                           "source_sha256": source_sha256})
    if active:
        raise ValueError("native phase log has unclosed intervals")
    if not events:
        raise ValueError("native phase log contains no complete intervals")
    return sorted(events, key=lambda item: (item["start"], item["end"], item["event_id"]))


def overlap_seconds(events: list[dict], start: float, end: float) -> float:
    """Union coverage, including simultaneous CPU/memory events only once."""
    intervals = sorted((max(start, event["start"]), min(end, event["end"])) for event in events
                       if event["end"] > start and event["start"] < end)
    total = 0.
    last = -math.inf
    for begin, finish in intervals:
        total += max(0., finish - max(begin, last))
        last = max(last, finish)
    return total


def classify_window(events: list[dict], node: str, start: float, end: float) -> dict:
    if end - start != 300:
        raise ValueError("interference labels require a five-minute resource window")
    own = [event for event in events if event["node"] == node]
    direct = overlap_seconds(own, start, end)
    global_seconds = overlap_seconds(events, start, end)
    guarded = [event for event in events if event["end"] > start - BOUNDARY_GUARD_SECONDS
               and event["start"] < end + BOUNDARY_GUARD_SECONDS]
    if direct > 0:
        label = "direct-recorded-interference"
    elif global_seconds > 0:
        label = "other-host-interference-context"
    elif guarded:
        label = "boundary-uncertain"
    else:
        label = "no-recorded-interference-reference"
    return {"label": label, "direct_interference_seconds": direct,
            "any_host_interference_seconds": global_seconds,
            "overlapping_or_guarded_event_ids": sorted(event["event_id"] for event in guarded)}


def audit_phase_labels(manifest_path: Path, resource_dir: Path, output: Path, *, project: Path | None = None) -> dict:
    commit = None
    if project is not None:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=project, text=True).strip():
            raise ValueError("commit the label implementation before freezing its evidence")
    manifest, files, _ = load_gamma_manifest(manifest_path)
    if manifest.get("phase_evidence_included") is not True:
        raise ValueError("native phase evidence scope is required")
    resource_report = json.loads((resource_dir / "resource-report.json").read_text())
    frame_blob = (resource_dir / "resource-windows.jsonl").read_bytes()
    frame_sha = hashlib.sha256(frame_blob).hexdigest()
    if (resource_report.get("source_manifest_sha256") != manifest_path.stem
        or resource_report.get("training_data_policy_hash") != manifest["training_data_policy_hash"]
        or resource_report.get("frame_sha256") != frame_sha
        or resource_report.get("feature_set_version") != "gamma-resource-5m/v1"
        or resource_report.get("activation_allowed") is not False):
        raise ValueError("resource frames/report do not match the verified phase acquisition")
    with files["meta-data/meta-data/placement.csv"].open(newline="") as stream:
        placement_rows = list(csv.reader(stream))
    if any(len(row) != 2 or not all(value.strip() for value in row) for row in placement_rows):
        raise ValueError("invalid native placement metadata")
    placement = {entity.strip(): node.strip() for entity, node in placement_rows}
    if len(placement) != len(placement_rows):
        raise ValueError("duplicate native entity in placement")
    event_catalog, capture_bounds = {}, {}
    source_refs = {"meta-data/meta-data/placement.csv": hashlib.sha256(
        files["meta-data/meta-data/placement.csv"].read_bytes()).hexdigest()}
    for experiment in manifest["selection"]:
        args_name = f"raw_dataset/{experiment}/args.txt"
        args_blob = files[args_name].read_bytes()
        args = json.loads(args_blob)
        targets = args.get("bottlenecked_nodes")
        if (not isinstance(targets, list) or not targets or any(not isinstance(node, str) or not node for node in targets)
            or len(set(targets)) != len(targets) or not set(targets) <= set(placement.values())):
            raise ValueError("phase targets must be unique known native placement nodes")
        prefix = f"raw_dataset/{experiment}/"
        phase_paths = {name.removeprefix(prefix).removesuffix("_phases"): name for name in files
                       if name.startswith(prefix) and phase_file(name)}
        if set(phase_paths) != set(targets):
            raise ValueError("phase log nodes must exactly cover declared interference targets")
        events = []
        source_refs[args_name] = hashlib.sha256(args_blob).hexdigest()
        for node, name in sorted(phase_paths.items()):
            blob = files[name].read_bytes()
            digest = hashlib.sha256(blob).hexdigest()
            source_refs[name] = digest
            events.extend(parse_phases(blob.decode(), node=node, source_sha256=digest))
        raw_bounds = []
        for name, path in files.items():
            if name.startswith(prefix + "prom_metrics/") and path.stat().st_size:
                values = validate_series(json.loads(path.read_text()))
                if values:
                    raw_bounds.append((values[0][0], values[-1][0]))
        if not raw_bounds:
            raise ValueError("phase audit requires observed raw resource clock bounds")
        lower, upper = min(begin for begin, _ in raw_bounds), max(finish for _, finish in raw_bounds)
        if any(event["start"] < lower - BOUNDARY_GUARD_SECONDS or event["end"] > upper + BOUNDARY_GUARD_SECONDS for event in events):
            raise ValueError("native phase intervals are outside the observed resource capture clock")
        capture_bounds[experiment] = {"first_observation": lower, "last_observation": upper}
        event_catalog[experiment] = sorted(events, key=lambda event: (event["start"], event["event_id"]))
    labels = []
    counts, eligible_counts = Counter(), Counter()
    seen = set()
    for line in frame_blob.splitlines():
        row = json.loads(line)
        validate_payload("research-resource-window.schema.json", row)
        if (row["source_manifest_sha256"] != manifest_path.stem
            or row["profile_sha256"] != resource_report["profile_sha256"]
            or row["experiment"] not in event_catalog or row["entity"] not in placement):
            raise ValueError("resource row has unknown or mismatched phase lineage")
        identity = (row["experiment"], row["entity"], row["window_end"])
        if identity in seen:
            raise ValueError("duplicate resource window in phase-label join")
        seen.add(identity)
        start, end = [datetime.fromisoformat(row[key].replace("Z", "+00:00")).timestamp()
                      for key in ("window_start", "window_end")]
        status = classify_window(event_catalog[row["experiment"]], placement[row["entity"]], start, end)
        label = {"schema_version": "1.0", "label_version": LABEL_VERSION,
                 **{key: row[key] for key in ("experiment", "entity", "window_start", "window_end")},
                 "hosting_node": placement[row["entity"]], "resource_window_sha256": hashlib.sha256(line).hexdigest(),
                 "source_manifest_sha256": manifest_path.stem, "boundary_guard_seconds": BOUNDARY_GUARD_SECONDS,
                 "eligible_for_features": row["eligible_for_features"],
                 "activation_allowed": False, "precursor_target_available": False, **status}
        validate_payload("research-interference-label.schema.json", label)
        labels.append(label)
        counts[status["label"]] += 1
        if row["eligible_for_features"]:
            eligible_counts[status["label"]] += 1
    if len(labels) != resource_report["rows"] or sum(eligible_counts.values()) != resource_report["eligible_feature_rows"]:
        raise ValueError("resource report counts differ from verified frames")
    label_blob = b"".join(canonical_bytes(row) + b"\n" for row in labels)
    report = {"report_version": "gamma-interference-audit/v1", "source_commit": commit,
              "label_version": LABEL_VERSION, "source_manifest_sha256": manifest_path.stem,
              "training_data_policy_hash": manifest["training_data_policy_hash"],
              "resource_frame_sha256": frame_sha, "resource_feature_values_sha256": resource_report["feature_values_sha256"],
              "resource_report_sha256": hashlib.sha256((resource_dir / "resource-report.json").read_bytes()).hexdigest(),
              "label_sha256": hashlib.sha256(label_blob).hexdigest(), "rows": len(labels),
              "label_counts": dict(counts), "feature_eligible_label_counts": dict(eligible_counts),
              "source_metadata_sha256": source_refs, "events": event_catalog, "raw_capture_bounds": capture_bounds,
              "boundary_guard_seconds": BOUNDARY_GUARD_SECONDS, "training_ready": False, "activation_allowed": False,
              "blocking_reasons": ["recorded interference is not independently measured SLO violation or recovery",
                  "no-recorded-interference reference does not certify operational health",
                  "independent source sessions and grouped development/calibration/holdout splits are not frozen",
                  "resource-only research features do not establish live HTTP parity or precursor targets"]}
    output.mkdir(parents=True, exist_ok=False)
    (output / "interference-labels.jsonl").write_bytes(label_blob)
    (output / "interference-report.json").write_bytes(canonical_bytes(report) + b"\n")
    return report
