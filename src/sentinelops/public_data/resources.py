"""Causal raw cAdvisor resource windows, separate from live HTTP/labels."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess

from sentinelops.contracts import validate_payload
from sentinelops.public_data.gamma import COUNTERS, METRICS, canonical_bytes, load_gamma_manifest


FEATURES = ("cpu_cores_mean_5m", "cpu_cores_slope_5m", "memory_bytes_mean_5m", "memory_bytes_slope_5m",
            "network_receive_bytes_per_second_mean_5m", "network_transmit_bytes_per_second_mean_5m")


def validate_profile(profile: dict) -> None:
    fixed = {"profile_version": "gamma-raw-resource/v1", "feature_set_version": "gamma-resource-5m/v1",
             "mode": "event-time-analytical", "bucket_seconds": 60, "lookback_seconds": 300,
             "step_seconds": 60, "minimum_valid_buckets": 4}
    if any(profile.get(key) != value for key, value in fixed.items()) or profile.get("ordered_features") != list(FEATURES):
        raise ValueError("resource profile differs from the versioned research feature contract")
    if not 0 < profile["minimum_time_coverage"] <= 1:
        raise ValueError("invalid resource coverage requirement")
    for key in ("maximum_sample_gap_seconds", "gauge_maximum_age_seconds"):
        if not 0 < profile[key] <= 60:
            raise ValueError("invalid resource gap/age bound")
    if not isinstance(profile["warmup_seconds"], int) or profile["warmup_seconds"] < 300:
        raise ValueError("resource windows require at least five minutes of source warmup")


def validate_series(series: list[list]) -> list[tuple[float, float]]:
    if any(not isinstance(row, (list, tuple)) or len(row) != 2 for row in series):
        raise ValueError("resource series requires time/value pairs")
    result = [(float(time), float(value)) for time, value in series]
    if any(not math.isfinite(time) or not math.isfinite(value) or value < 0 for time, value in result):
        raise ValueError("resource observations must be finite and nonnegative")
    if any(right[0] <= left[0] for left, right in zip(result, result[1:])):
        raise ValueError("resource observation times must strictly increase")
    return result


def metric_bucket(series, begin: float, end: float, *, counter: bool, max_gap: float, max_age: float) -> dict:
    # Filtering precedes every computation. Future samples cannot establish
    # coverage, interpolate boundaries, detect resets or refresh a stale gauge.
    prefix = [(time, value) for time, value in series if time <= end]
    integral = coverage = 0.
    reasons = set()
    used = []
    if counter:
        for left, right in zip(prefix, prefix[1:]):
            start, finish = max(begin, left[0]), min(end, right[0])
            if finish <= start:
                continue
            elapsed = right[0] - left[0]
            if right[1] < left[1]:
                reasons.add("counter-reset")
                continue
            if elapsed > max_gap:
                reasons.add("sample-gap")
                continue
            integral += (right[1] - left[1]) / elapsed * (finish - start)
            coverage += finish - start
            used.extend((left[0], right[0]))
    else:
        for index, (time, value) in enumerate(prefix):
            next_time = prefix[index + 1][0] if index + 1 < len(prefix) else end
            start, finish = max(begin, time), min(end, next_time, time + max_age)
            if finish > start:
                integral += value * (finish - start)
                coverage += finish - start
                used.append(time)
    return {"integral": integral, "covered_seconds": coverage, "coverage": coverage / (end - begin),
            "value": integral / coverage if coverage else None, "reasons": sorted(reasons),
            "max_input_time": max(used) if used else None}


def slope(points: list[tuple[float, float]]) -> float:
    origin = points[0][0]
    x = [(time - origin) / 60 for time, _ in points]
    y = [value for _, value in points]
    mean_x, mean_y = sum(x) / len(x), sum(y) / len(y)
    denominator = sum((value - mean_x) ** 2 for value in x)
    return sum((left - mean_x) * (right - mean_y) for left, right in zip(x, y)) / denominator


def resource_window(series_by_metric: dict, end: float, profile: dict) -> dict:
    validate_profile(profile)
    if set(series_by_metric) != set(METRICS) or end % 60:
        raise ValueError("resource window requires all raw metrics and an aligned cutoff")
    valid = []
    coverage = []
    reasons = set()
    used = []
    for finish in range(int(end) - 240, int(end) + 1, 60):
        bucket = {}
        for metric in METRICS:
            point = metric_bucket(series_by_metric[metric], finish - 60, finish, counter=metric in COUNTERS,
                                  max_gap=profile["maximum_sample_gap_seconds"], max_age=profile["gauge_maximum_age_seconds"])
            coverage.append(point["coverage"])
            reasons.update(point["reasons"])
            if point["max_input_time"] is not None:
                used.append(point["max_input_time"])
            bucket[metric] = point
        if all(point["coverage"] + 1e-12 >= profile["minimum_time_coverage"] for point in bucket.values()):
            valid.append((finish, bucket))
        else:
            reasons.add("insufficient-time-coverage")
    features = None
    eligible = len(valid) >= profile["minimum_valid_buckets"]
    if eligible:
        def mean(metric):
            return sum(bucket[metric]["integral"] for _, bucket in valid) / sum(bucket[metric]["covered_seconds"] for _, bucket in valid)
        cpu, memory = "container_cpu_usage_seconds_total", "container_memory_usage_bytes"
        features = dict(zip(FEATURES, (mean(cpu), slope([(time, bucket[cpu]["value"]) for time, bucket in valid]),
            mean(memory), slope([(time, bucket[memory]["value"]) for time, bucket in valid]),
            mean("container_network_receive_bytes_total"), mean("container_network_transmit_bytes_total"))))
    return {"eligible_for_features": eligible, "features": features,
            "max_input_time": max(used) if used else None,
            "quality": {"valid_buckets": len(valid), "required_buckets": profile["minimum_valid_buckets"],
                        "reasons": sorted(reasons), "minimum_metric_coverage": min(coverage)}}


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def window_cutoffs(series_by_metric: dict, profile: dict) -> range:
    validate_profile(profile)
    if set(series_by_metric) != set(METRICS) or not all(series_by_metric.values()):
        raise ValueError("resource cutoffs require four nonempty native metric series")
    first = math.ceil(max(points[0][0] for points in series_by_metric.values()) / 60) * 60
    last = math.floor(min(points[-1][0] for points in series_by_metric.values()) / 60) * 60
    return range(first + profile["warmup_seconds"] + 300, last + 1, 60)


def build_resource_windows(manifest_path: Path, profile_path: Path, output: Path, *, project: Path | None = None) -> dict:
    profile_blob = profile_path.read_bytes()
    profile = json.loads(profile_blob)
    validate_profile(profile)
    commit = None
    if project is not None:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=project, text=True).strip():
            raise ValueError("commit the resource implementation/profile before freezing features")
    manifest, files, _ = load_gamma_manifest(manifest_path)
    profile_sha = hashlib.sha256(profile_blob).hexdigest()
    output.mkdir(parents=True, exist_ok=False)
    counts = Counter()
    excluded = Counter()
    input_refs = defaultdict(dict)
    rows = []
    for experiment in manifest["selection"]:
        series = defaultdict(dict)
        with files["meta-data/meta-data/placement.csv"].open(newline="") as stream:
            placement = list(csv.reader(stream))
        if any(len(item) != 2 or not item[0].strip() or not item[1].strip() for item in placement):
            raise ValueError("invalid GAMMA placement metadata")
        native_entities = [item[0].strip() for item in placement]
        if len(native_entities) != len(set(native_entities)):
            raise ValueError("placement must identify each native entity once")
        entities = set(native_entities) - set(profile["excluded_entities"])
        prefix = f"raw_dataset/{experiment}/prom_metrics/"
        for name, path in files.items():
            if not name.startswith(prefix):
                continue
            native = name.removeprefix(prefix)
            entity, separator, suffix = native.partition("_container_")
            metric = "container_" + suffix
            if not separator or metric not in METRICS:
                raise ValueError("unrecognized resource file identity")
            if entity not in entities:
                excluded["excluded-entity-series"] += 1
                continue
            values = validate_series(json.loads(path.read_text())) if path.stat().st_size else []
            if values and (values[0][0] < 1_000_000_000 or values[-1][0] >= 10_000_000_000):
                raise ValueError("raw Prometheus series is not Unix seconds")
            series[entity][metric] = values
            input_refs[f"{experiment}/{entity}"][metric] = hashlib.sha256(path.read_bytes()).hexdigest()
        for entity in sorted(entities):
            native = series.get(entity, {})
            if set(native) != set(METRICS) or not all(native.values()):
                excluded["missing-required-series"] += 1
                continue
            for end in window_cutoffs(native, profile):
                result = resource_window(native, end, profile)
                latest = result.pop("max_input_time")
                row = {"schema_version": "1.0", "feature_set_version": profile["feature_set_version"],
                    "source_profile": profile["profile_version"], "mode": profile["mode"],
                    "experiment": experiment, "entity": entity, "window_start": iso(end - 300), "window_end": iso(end),
                    "max_input_observed_at": iso(latest) if latest is not None else None,
                    "source_manifest_sha256": manifest_path.stem, "profile_sha256": profile_sha,
                    "activation_allowed": False, "arrival_times_verified": False, **result}
                validate_payload("research-resource-window.schema.json", row)
                rows.append(row)
                counts[f"{entity}/all"] += 1
                counts[f"{entity}/eligible"] += int(row["eligible_for_features"])
                if not row["eligible_for_features"]:
                    excluded["insufficient-time-coverage-window"] += 1
    frames = b"".join(canonical_bytes(row) + b"\n" for row in rows)
    value_fields = ("experiment", "entity", "window_start", "window_end", "max_input_observed_at",
                    "eligible_for_features", "features", "quality")
    value_payload = {"feature_set_version": profile["feature_set_version"], "profile_sha256": profile_sha,
                     "windows": [{key: row[key] for key in value_fields} for row in rows]}
    (output / "resource-windows.jsonl").write_bytes(frames)
    report = {"report_version": "gamma-resource-windows/v1", "source_commit": commit,
        "source_manifest_sha256": manifest_path.stem, "profile_sha256": profile_sha,
        "training_data_policy_hash": manifest["training_data_policy_hash"],
        "feature_set_version": profile["feature_set_version"], "ordered_features": list(FEATURES),
        "frame_sha256": hashlib.sha256(frames).hexdigest(), "rows": len(rows),
        "feature_values_sha256": hashlib.sha256(canonical_bytes(value_payload)).hexdigest(),
        "eligible_feature_rows": sum(row["eligible_for_features"] for row in rows), "entity_counts": dict(counts),
        "exclusions": dict(excluded), "raw_input_sha256": dict(input_refs),
        "activation_allowed": False, "training_ready": False,
        "blocking_reasons": ["feature eligibility is not a healthy/anomaly/precursor label",
            "independent source sessions and grouped development/calibration/holdout splits are not frozen",
            "no live HTTP parity or arrival-time evidence; no online lead-time claim"]}
    (output / "resource-report.json").write_bytes(canonical_bytes(report) + b"\n")
    return report
