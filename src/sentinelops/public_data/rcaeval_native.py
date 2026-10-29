"""Bounded source-native observations, without evaluator labels or live SLO claims."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

from sentinelops.public_data.downloads import verify_bytes
from sentinelops.public_data.rcaeval_partitions import INSPECTED, REVISION, canonical, require_manifest_files

PROFILE_SHA256 = "4e9e5e2e57d01d0c59ac0de450612c90fb9d39d2a0f47370559a6568a991f3a1"
UINT64_MAX = 2**64 - 1
TRACE_STRINGS = {"time", "traceID", "spanID", "serviceName", "methodName", "operationName", "parentSpanID"}
TRACE_INTS = {"startTimeMillis", "startTime", "duration", "statusCode"}


class NativeSourceError(ValueError):
    """A source incompatibility with a fixed, non-PII quarantine reason."""


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def validate_profile(profile: dict) -> None:
    if digest(profile) != PROFILE_SHA256:
        raise NativeSourceError("native profile differs from the frozen premeasurement protocol")


def _bounded_bytes(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise NativeSourceError("source byte budget exceeded")
    return data


def load_native_inputs(manifest_path: Path, profile: dict) -> tuple[dict, dict[str, dict[str, bytes]]]:
    """Admit the complete inventory first; decode only retained, checksum-verified bytes."""
    validate_profile(profile)
    blob = _bounded_bytes(manifest_path, profile["maximum_manifest_bytes"])
    if hashlib.sha256(blob).hexdigest() != manifest_path.stem:
        raise NativeSourceError("source manifest checksum mismatch")
    manifest = json.loads(blob)
    if (not isinstance(manifest, dict) or manifest.get("manifest_version") != "public-download/v1"
        or manifest.get("dataset") != "RCAEval" or manifest.get("revision") != REVISION
        or manifest.get("activation_allowed") is not False):
        raise NativeSourceError("unsupported native RCAEval source manifest")
    targets = require_manifest_files(manifest, manifest_path.parent.parent.resolve())
    if not set(manifest["selection"]) <= set(INSPECTED):
        raise NativeSourceError("native profile permits only previously inspected development cases")
    for record in manifest["files"]:
        checksum = record.get("publisher_checksum")
        if (not isinstance(checksum, dict) or type(checksum.get("size")) is not int or checksum["size"] < 0
            or not isinstance(record.get("sha256"), str) or not re.fullmatch("[0-9a-f]{64}", record["sha256"])
            or not ((isinstance(checksum.get("sha256"), str) and re.fullmatch("[0-9a-f]{64}", checksum["sha256"]))
                    or (isinstance(checksum.get("git_blob"), str) and re.fullmatch("[0-9a-f]{40}", checksum["git_blob"])) )):
            raise NativeSourceError("native source requires complete publisher checksum metadata")
    sizes = [path.stat().st_size for path in targets]
    if any(size > profile["maximum_file_bytes"] for size in sizes) or sum(sizes) > profile["maximum_total_file_bytes"]:
        raise NativeSourceError("source byte budget exceeded")
    result = {case: {} for case in manifest["selection"]}
    total = 0
    for record, path in zip(manifest["files"], targets):
        data = _bounded_bytes(path, profile["maximum_file_bytes"])
        total += len(data)
        if total > profile["maximum_total_file_bytes"]:
            raise NativeSourceError("source byte budget exceeded")
        if verify_bytes(data, record["publisher_checksum"]) != record["sha256"]:
            raise NativeSourceError("native source checksum differs")
        if path.parent.name in result and path.name in ("metrics.parquet", "logs.parquet", "traces.parquet"):
            result[path.parent.name][path.stem] = data
    if any("metrics" not in tables for tables in result.values()):
        raise NativeSourceError("every selected case requires admitted metrics")
    return manifest, result


def _text(value, *, nullable=False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise NativeSourceError("native identity/text is missing or malformed")
    return value


def _integer(value, multiplier=1) -> int:
    if type(value) is not int or not 0 <= value <= UINT64_MAX // multiplier:
        raise NativeSourceError("native clock/count requires an exact nonnegative bounded integer")
    return value * multiplier


def _table(data: bytes, modality: str, profile: dict):
    import pyarrow as pa
    import pyarrow.parquet as pq
    if len(data) > profile["maximum_file_bytes"]:
        raise NativeSourceError("source byte budget exceeded")
    table = pq.ParquetFile(pa.BufferReader(data))
    names = table.schema_arrow.names
    if len(set(names)) != len(names) or table.metadata.num_rows > profile["maximum_rows_per_table"]:
        raise NativeSourceError("native table row/schema budget exceeded")
    if modality == "metrics":
        if "time" not in names or not 2 <= len(names) <= profile["maximum_metric_columns"]:
            raise NativeSourceError("native metric column schema/budget differs")
        if table.schema_arrow.field("time").type != pa.int64() or any(
            table.schema_arrow.field(n).type != pa.float64() for n in names if n != "time"
        ):
            raise NativeSourceError("native metric types differ")
    else:
        strings = {"container_name", "message"} if modality == "logs" else TRACE_STRINGS
        ints = {"timestamp"} if modality == "logs" else TRACE_INTS
        if set(names) != strings | ints or any(
            not (pa.types.is_string(table.schema_arrow.field(n).type) or pa.types.is_large_string(table.schema_arrow.field(n).type))
            for n in strings
        ) or any(table.schema_arrow.field(n).type != pa.int64() for n in ints):
            raise NativeSourceError("native modality schema/types differ")
    # Footer limits apply before Arrow allocates decoded row groups.
    if sum(table.metadata.row_group(i).total_byte_size for i in range(table.num_row_groups)) > profile["maximum_decoded_bytes_per_table"]:
        raise NativeSourceError("native decoded byte budget exceeded")
    return table


def _rows(table, profile: dict):
    total, count = 0, 0
    for batch in table.iter_batches(batch_size=profile["batch_rows"]):
        total += batch.nbytes
        count += len(batch)
        if total > profile["maximum_decoded_bytes_per_table"] or count > profile["maximum_rows_per_table"]:
            raise NativeSourceError("native decoded byte/row budget exceeded")
        yield from batch.to_pylist()


def adapt_native_case(tables: dict[str, bytes], profile: dict) -> dict:
    """No case name, root, fault or injection metadata enters this business function."""
    validate_profile(profile)
    if not isinstance(tables, dict) or "metrics" not in tables or not set(tables) <= {"metrics", "logs", "traces"}:
        raise NativeSourceError("native adapter requires only explicit telemetry modalities")
    if any(not isinstance(v, bytes) for v in tables.values()) or sum(map(len, tables.values())) > profile["maximum_total_file_bytes"]:
        raise NativeSourceError("native telemetry byte budget exceeded")
    parsed = {name: _table(data, name, profile) for name, data in tables.items()}
    metric = parsed["metrics"]
    if not metric.metadata.num_rows:
        raise NativeSourceError("native metrics are empty")
    # Only the time column is read to choose a window; no label/injection clock.
    times = []
    for batch in metric.iter_batches(batch_size=profile["batch_rows"], columns=["time"]):
        times.extend(_integer(v, 1_000_000_000) for v in batch.column(0).to_pylist())
    if any(a >= b for a, b in zip(times, times[1:])):
        raise NativeSourceError("native metric time must be strictly increasing")
    end = times[-1] + 1_000_000_000
    if end > UINT64_MAX:
        raise NativeSourceError("native cutoff overflows clock")
    start = end - profile["analysis_window_seconds"] * 1_000_000_000
    if start < 0:
        raise NativeSourceError("native capture cannot support its fixed event-time window")
    entities, channels = set(), {}
    for name in metric.schema_arrow.names:
        if name == "time":
            continue
        entity, sep, suffix = name.rpartition("_")
        if not sep or not entity or not suffix:
            raise NativeSourceError("native metric lacks explicit service_metric identity")
        entities.add(entity)
        if len(entities) > profile["maximum_entities"]:
            raise NativeSourceError("native entity budget exceeded")
        channels[name] = {"entity": entity, "native_metric": suffix, "unit": None,
                          "window_observed": 0, "window_null": 0, "window_nan": 0,
                          "minimum": None, "maximum": None, "last": None}
    summaries = {}
    metric_hash = hashlib.sha256()
    for row in _rows(metric, profile):
        clock = _integer(row["time"], 1_000_000_000)
        normalized = [clock]
        for name, stats in channels.items():
            value = row[name]
            if value is None:
                normalized.append(None)
                key = "window_null"
            elif math.isnan(value):
                normalized.append({"native_nan": True})
                key = "window_nan"
            elif not math.isfinite(value):
                raise NativeSourceError("infinite native metric value")
            else:
                normalized.append(value)
                key = "window_observed"
            if start <= clock < end:
                stats[key] += 1
                if key == "window_observed":
                    stats["minimum"] = value if stats["minimum"] is None else min(stats["minimum"], value)
                    stats["maximum"] = value if stats["maximum"] is None else max(stats["maximum"], value)
                    stats["last"] = value
        metric_hash.update(canonical(normalized) + b"\n")
    summaries["metrics"] = {"present": True, "rows": len(times), "normalized_sha256": metric_hash.hexdigest(),
                            "channels": channels, "source_start_ns": times[0], "source_end_ns": times[-1]}
    span_nodes, log_entities, trace_entities = {}, {}, {}
    marker = re.compile("|".join("(?:" + p + ")" for p in profile["exception_markers"]), re.MULTILINE)
    for modality in ("logs", "traces"):
        if modality not in parsed:
            summaries[modality] = {"present": False, "rows": 0, "missing_reason": "not-published-in-admitted-inventory"}
            continue
        fingerprint, statuses = hashlib.sha256(), Counter()
        count = backwards = eligible = 0
        previous = minimum_clock = maximum_clock = None
        for row in _rows(parsed[modality], profile):
            count += 1
            if modality == "logs":
                clock = _integer(row["timestamp"], 1_000_000_000)
                entity = _text(row["container_name"])
                message = row["message"]
                if message is not None and not isinstance(message, str):
                    raise NativeSourceError("native log message type differs")
                match = bool(marker.search(message)) if message is not None else None
                normalized = [clock, entity, hashlib.sha256(message.encode()).hexdigest() if message is not None else None, match]
                if start <= clock < end:
                    stats = log_entities.setdefault(entity, {"records": 0, "messages": 0, "exception_markers": 0})
                    stats["records"] += 1
                    stats["messages"] += message is not None
                    stats["exception_markers"] += match is True
                    eligible += 1
            else:
                clock = _integer(row["startTime"], 1000)
                duration = _integer(row["duration"], 1000)
                if _integer(row["startTimeMillis"]) != row["startTime"] // 1000 or clock + duration > UINT64_MAX:
                    raise NativeSourceError("native trace unit pair/end clock differs")
                entity = _text(row["serviceName"])
                trace, span = _text(row["traceID"]), _text(row["spanID"])
                parent = _text(row["parentSpanID"], nullable=True)
                key = (trace, span)
                if key in span_nodes:
                    raise NativeSourceError("duplicate native trace/span identity")
                if len(span_nodes) >= profile["maximum_unique_span_ids"]:
                    raise NativeSourceError("native span identity budget exceeded")
                if parent == span:
                    raise NativeSourceError("self-parent native span")
                in_window = start <= clock < end and clock + duration <= end
                span_nodes[key] = (entity, parent, in_window)
                for name in ("time", "methodName", "operationName"):
                    if row[name] is not None and not isinstance(row[name], str):
                        raise NativeSourceError("native trace text type differs")
                if row["statusCode"] is not None and (type(row["statusCode"]) is not int or not -2**63 <= row["statusCode"] < 2**63):
                    raise NativeSourceError("native status must preserve its nullable int64 type")
                statuses[str(row["statusCode"]) if row["statusCode"] is not None else "null"] += 1
                normalized = {"start_unix_ns": clock, "duration_ns": duration,
                              "native_fields": [row[n] for n in sorted(row)]}
                if in_window:
                    stats = trace_entities.setdefault(entity, {"spans": 0, "recorded_duration_ns": 0})
                    stats["spans"] += 1
                    stats["recorded_duration_ns"] += duration
                    eligible += 1
            if not times[0] <= clock < end:
                raise NativeSourceError("native modality clock is outside the metric capture; units unverified")
            backwards += previous is not None and clock < previous
            previous = clock
            minimum_clock = clock if minimum_clock is None else min(minimum_clock, clock)
            maximum_clock = clock if maximum_clock is None else max(maximum_clock, clock)
            entities.add(entity)
            if len(entities) > profile["maximum_entities"]:
                raise NativeSourceError("native entity budget exceeded")
            fingerprint.update(canonical(normalized) + b"\n")
        summaries[modality] = {"present": True, "rows": count, "eligible_window_records": eligible,
                               "source_start_ns": minimum_clock, "source_end_ns": maximum_clock,
                               "backwards_adjacent_times": backwards, "normalized_sha256": fingerprint.hexdigest()}
        if modality == "traces":
            summaries[modality]["native_status_counts"] = dict(sorted(statuses.items()))
    edges, unresolved, roots = Counter(), 0, 0
    for (trace, _), (entity, parent, in_window) in span_nodes.items():
        if not in_window:
            continue
        if parent is None:
            roots += 1
        elif (trace, parent) not in span_nodes:
            unresolved += 1
        else:
            consumer = span_nodes[(trace, parent)][0]
            edges[(consumer, entity)] += 1
    source = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(tables.items())}
    scope = {"scope_version": "rcaeval-native-observations/v1", "profile_sha256": PROFILE_SHA256,
             "source_telemetry_sha256": source, "window_start_ns": start, "window_end_ns": end,
             "receipt_clock": None, "entities": sorted(entities), "modalities": summaries,
             "log_support": dict(sorted(log_entities.items())), "trace_support": dict(sorted(trace_entities.items())),
             "observed_parent_links": [{"consumer": a, "dependency": b, "spans": n} for (a, b), n in sorted(edges.items())],
             "unresolved_parent_links": unresolved, "root_spans": roots,
             "live_rca_compatible": False, "actions_allowed": False}
    scope["scope_sha256"] = digest(scope)
    return scope
