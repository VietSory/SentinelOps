"""Inspect real GAMMA capabilities without inventing HTTP or precursor labels."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import re

from sentinelops.public_data.gamma import COUNTERS, REQUEST_TYPES, experiment_id, load_gamma_manifest


def audit_gamma(manifest_path: Path) -> dict:
    import numpy as np
    import pandas as pd

    manifest, files, inventory = load_gamma_manifest(manifest_path)
    catalog = defaultdict(set)
    for item in inventory["processed_catalog"]:
        identifier = experiment_id(item["name"])
        if identifier:
            catalog[identifier].add(item["name"].split("/")[1])
    mappings = {}
    for kind in REQUEST_TYPES:
        path = files[f"meta-data/meta-data/{kind}_ms_rpc_map.csv"]
        with path.open(newline="") as stream:
            rows = list(csv.reader(stream))
        if not rows:
            raise ValueError("GAMMA RPC mapping is empty")
        invalid = [{"zero_based_row": index, "parsed_fields": len(row)} for index, row in enumerate(rows)
                   if len(row) != 2 or not all(cell.strip() for cell in row)]
        normalized = [tuple(cell.strip() for cell in row) for row in rows if len(row) == 2 and all(cell.strip() for cell in row)]
        mappings[kind] = {"source_mapping_rows": len(rows), "unique_rpc_service_pairs": len(set(normalized)),
                          "duplicate_pair_rows": len(normalized) - len(set(normalized)),
                          "ambiguous_mapping_rows": invalid}

    experiments = []
    for identifier in manifest["selection"]:
        args = json.loads(files[f"raw_dataset/{identifier}/args.txt"].read_text())
        if not isinstance(args, dict):
            raise ValueError("invalid GAMMA experiment args")
        phases = args.get("phases")
        metrics = []
        for name, path in files.items():
            if not name.startswith(f"raw_dataset/{identifier}/prom_metrics/"):
                continue
            source = json.loads(path.read_text()) if path.stat().st_size else []
            if not isinstance(source, list):
                raise ValueError("GAMMA raw metric is not a time/value series")
            detail = {"source_path": name, "rows": len(source), "empty": not source}
            if source:
                if any(not isinstance(row, list) or len(row) != 2 for row in source):
                    raise ValueError("GAMMA raw metric has malformed observations")
                values = np.asarray(source, dtype=float)
                if not np.isfinite(values).all() or np.any(values[:, 1] < 0):
                    raise ValueError("GAMMA resource metric has invalid values")
                intervals = np.diff(values[:, 0])
                if np.any(intervals <= 0):
                    raise ValueError("GAMMA raw metric timestamps are not strictly increasing")
                detail.update({"time_start_raw": float(values[0, 0]), "time_end_raw": float(values[-1, 0]),
                    "median_sample_interval_raw": float(np.median(intervals)) if len(intervals) else None,
                    "max_sample_interval_raw": float(intervals.max()) if len(intervals) else None,
                    "counter_reset_count": int((np.diff(values[:, 1]) < 0).sum()) if name.endswith(COUNTERS) else None})
            metrics.append(detail)
        processed = []
        for name, path in files.items():
            if experiment_id(name) != identifier:
                continue
            kind = name.split("/")[1]
            header = pd.read_csv(path, nrows=0).columns.tolist()
            groups = {kind: {col.split("_")[0] for col in header if re.fullmatch(r"[0-9]+_" + suffix, col)}
                      for kind, suffix in (("latency", "latency"), ("start", "start"), ("labels", "label_RPC"))}
            if not groups["latency"] or groups["latency"] != groups["start"] or groups["start"] != groups["labels"]:
                raise ValueError("GAMMA processed RPC columns have inconsistent identities")
            if not {"window_id", "trace_id", "label_trace"} <= set(header) or "0_start" not in header:
                raise ValueError("GAMMA processed data lacks trace/time/label fields")
            label_cols = [col for col in header if col.endswith("_label_RPC")] + ["label_trace"]
            time_cols = [col for col in header if re.fullmatch(r"[0-9]+_(start|latency)", col)]
            rows = positive = backwards = missing = mismatch = 0
            previous = None
            minimum, maximum = float("inf"), float("-inf")
            windows = set()
            root_durations = []
            for chunk in pd.read_csv(path, chunksize=10000, dtype={"trace_id": str, "window_id": str}):
                if chunk.empty:
                    continue
                labels = chunk[label_cols].to_numpy(dtype=float)
                times = chunk[time_cols].to_numpy(dtype=float)
                if not np.isin(labels, [0, 1]).all() or not np.isfinite(times).all() or np.any(times < 0):
                    raise ValueError("GAMMA processed labels or RPC time values are invalid")
                numeric = chunk.drop(columns=["trace_id", "window_id"]).to_numpy(dtype=float)
                if np.isinf(numeric).any():
                    raise ValueError("infinite GAMMA processed metric")
                missing += int(np.isnan(numeric).sum())
                current = chunk["0_start"].to_numpy(dtype=float)
                backwards += int((np.diff(current) < 0).sum())
                if previous is not None and current[0] < previous:
                    backwards += 1
                previous = current[-1]
                minimum, maximum = min(minimum, float(current.min())), max(maximum, float(current.max()))
                windows.update(chunk.window_id)
                rows += len(chunk)
                positive += int(chunk.label_trace.sum())
                mismatch += int(((chunk[label_cols[:-1]].max(axis=1) > 0) != (chunk.label_trace > 0)).sum())
                root_durations.extend(chunk["0_latency"].to_numpy(dtype=float))
            if not rows:
                raise ValueError("empty GAMMA processed table")
            processed.append({"source_path": name, "request_type": kind, "rows": rows,
                "rpc_columns": len(groups["latency"]), "mapping_rows": mappings[kind]["source_mapping_rows"],
                "root_time_min_raw": minimum, "root_time_max_raw": maximum,
                "decreasing_root_time_transitions": backwards, "window_ids": len(windows),
                "normal_trace_labels": rows - positive, "anomalous_trace_labels": positive,
                "trace_vs_any_rpc_label_disagreements": mismatch, "numeric_missing_cells": missing,
                "root_latency_quantiles_raw": {str(q): float(np.quantile(root_durations, q)) for q in (.5, .95, .99)},
                "http_status_columns": [col for col in header if "http" in col.lower() and "status" in col.lower()],
                "rpc_service_mapping_verified": False, "time_duration_units_verified": False})
        experiments.append({"experiment": identifier, "args_fields": sorted(args),
            "declared_duration": args.get("experiment_duration"), "declared_warmup": args.get("warm_up_duration"),
            "declared_phase_count": len(phases) if isinstance(phases, list) else None,
            "raw_metric_files": len(metrics), "empty_metric_files": sum(item["empty"] for item in metrics),
            "raw_metrics": metrics, "processed_tables": processed,
            "graph_path_metadata_files": sum(name.startswith(f"raw_dataset/{identifier}/processed_traces/") for name in files)})
    return {"audit_version": "gamma-source-audit/v1", "dataset": "GAMMA", "source_version": 1,
        "source_manifest_sha256": manifest_path.stem, "inventory_sha256": manifest["inventory_sha256"],
        "training_data_policy_hash": manifest["training_data_policy_hash"],
        "catalog_experiment_names": len(catalog),
        "catalog_names_with_all_request_types": sum(set(REQUEST_TYPES) <= kinds for kinds in catalog.values()),
        "catalog_request_type_counts": dict(Counter(kind for kinds in catalog.values() for kind in kinds)),
        "verified_files": len(files), "expanded_bytes": sum(item["size"] for item in manifest["files"]),
        "rpc_mappings": mappings, "experiments": experiments, "activation_allowed": False,
        "training_ready": {"isolation_forest": False, "xgboost": False, "live_http_red": False},
        "blocking_reasons": ["RPC/node/service alignment and time/duration units require semantic verification",
            "processed traces omit the live HTTP status/error denominator; request sampling/completeness is unverified",
            "resource feature profile and grouped splits exist, but do not certify health or independent acquisition sessions",
            "source bottleneck labels and phase schedules do not independently establish SLO/gradual precursor labels"],
        "limitations": ["capability audit of selected experiments, not the complete GAMMA corpus",
            "published experiment names are not proof of independent acquisition sessions",
            "SHA256 freezes acquired bytes; publisher does not supply an independent file checksum",
            "no model was trained or activated by this audit"]}
