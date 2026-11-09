"""Build ADR-016 inputs from a bounded, immutable accepted health history."""
from __future__ import annotations

from datetime import timedelta
import math

from sentinelops.contracts import utc_time
from sentinelops.ml.alibaba_training import service_windows_fast
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2, decode_capture, mean_latency_ms


def project_relative_window(store, batch_ref, service, *, as_of, config):
    """Project the final window only; missing or late observations never become zeros."""
    if service not in BUSINESS_ROUTES:
        raise ValueError("relative projection requires a registered business service")
    anchor = store.read(batch_ref)
    identity = anchor["identity"]
    stream = anchor["stream_id"]
    if (identity["source_profile"] != SOURCE_PROFILE_V2 or stable_id(identity) != stream
        or identity["worker_version"] != "continuous-health/v1"):
        raise ValueError("relative projection requires a pinned v2 health stream")
    end = utc_time(anchor["bucket_end"])
    if end.second or end.microsecond:
        raise ValueError("relative projection requires UTC minute-aligned buckets")
    if end > as_of or utc_time(anchor["evaluated_at"]) > as_of:
        return None

    window = config["window_minutes"]
    count = window + config["baseline"]["max_minutes"]
    rows = {}
    batch = anchor
    for offset in range(count):
        bucket_end = end - timedelta(minutes=offset)
        if (batch["stream_id"] != stream or batch["identity"] != identity
            or utc_time(batch["bucket_end"]) != bucket_end):
            raise ValueError("relative history skips or changes the accepted health stream")
        captures = [(ref, store.read(ref)) for ref in batch["current_raw_refs"]]
        routes = {raw["service"] for _, raw in captures}
        if ((captures and (len(captures) != len(BUSINESS_ROUTES) or routes != set(BUSINESS_ROUTES)))
            or any(raw["source_profile"] != SOURCE_PROFILE_V2 or utc_time(raw["bucket_end"]) != bucket_end
                   for _, raw in captures)):
            raise ValueError("relative history contains foreign or duplicated raw captures")
        if utc_time(batch["evaluated_at"]) <= as_of:
            for ref, raw in captures:
                if raw["service"] != service or utc_time(raw["collected_at"]) > as_of:
                    continue
                try:
                    observation = decode_capture(raw, environment=identity["environment"])
                    mean = mean_latency_ms(raw)
                except (ValueError, KeyError, TypeError):
                    continue
                if (observation["quality"]["eligible_for_inference"] and mean is not None
                    and math.isfinite(mean) and mean > 0):
                    rows[int(bucket_end.timestamp()) // 60] = (mean, observation["measurements"]["request_count"], ref)
        if not batch["previous_batch_ref"] or offset == count - 1:
            break
        batch = store.read(batch["previous_batch_ref"])

    anchor_minute = int(end.timestamp()) // 60
    values = relative_feature_values({minute: (rt, rate) for minute, (rt, rate, _) in rows.items()},
                                     anchor_minute, config)
    if values is None:
        return None
    return {
        "projection_version": "accepted-alibaba-feature/v1", "feature_set_version": config["feature_set_version"],
        "service": service, "source_profile": SOURCE_PROFILE_V2, "batch_ref": batch_ref,
        "window_start": timestamp(end - timedelta(minutes=window)), "window_end": timestamp(end),
        "as_of": timestamp(as_of), "raw_refs": [rows[minute][2] for minute in sorted(rows)],
        "features": dict(zip(config["features"], values)),
    }


def relative_feature_values(minutes, anchor, config):
    """Call the shared training transform on only the bounded past of this anchor."""
    import pandas as pd

    window, baseline = config["window_minutes"], config["baseline"]
    first = anchor - window - baseline["max_minutes"] + 1
    selected = {minute: values for minute, values in minutes.items() if first <= minute <= anchor}
    if anchor not in selected or len(selected) < baseline["min_minutes"] + window - 1:
        return None
    series = pd.DataFrame.from_dict(selected, orient="index", columns=["rt", "rate"]).sort_index()
    projected = service_windows_fast(series, config)
    return projected[-1]["features"] if projected and projected[-1]["minute"] == anchor else None
