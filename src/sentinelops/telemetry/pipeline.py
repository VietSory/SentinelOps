from __future__ import annotations

from datetime import datetime
from typing import Any

from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import BUSINESS_ROUTES, build_feature_window, timestamp
from sentinelops.telemetry.prometheus import SOURCE_PROFILE, decode_capture
from sentinelops.telemetry.store import ArtifactStore


def process_captures(captures: list[dict[str, Any]], store: ArtifactStore, *, environment: str = "local") -> dict[str, Any]:
    observations = []
    raw_refs, observation_refs, feature_refs, quarantine_refs = [], [], [], []
    seen = set()
    for capture in captures:
        raw_ref = store.write("raw", capture)
        if raw_ref in seen:
            continue
        seen.add(raw_ref)
        raw_refs.append(raw_ref)
        try:
            item = decode_capture(capture, environment=environment)
        except (ValueError, KeyError, TypeError) as exc:
            quarantine_refs.append(store.write("quarantine", {"raw_ref": raw_ref, "reason": str(exc)}))
            continue
        observations.append(item)
        observation_refs.append(store.write("observations", item))
    windows = sorted({utc_time(item["bucket_end"]) for item in observations})
    profiles = {item["source_profile"] for item in observations}
    if len(profiles) > 1:
        raise ValueError("a capture manifest cannot mix source profiles")
    profile = next(iter(profiles), SOURCE_PROFILE)
    as_of = max((utc_time(item["available_at"]) for item in observations), default=None)
    eligible = 0
    if as_of:
        for end in windows:
            for service in BUSINESS_ROUTES:
                feature = build_feature_window(observations, service=service, window_end=end,
                                               as_of=as_of, environment=environment, source_profile=profile)
                feature_refs.append(store.write("features", feature))
                eligible += int(feature["quality"]["eligible_for_inference"])
    manifest = {
        "manifest_version": "1.0", "environment": environment,
        "source_profile": profile, "replay_mode": "availability-bounded-batch",
        "raw_refs": sorted(set(raw_refs)), "observation_refs": sorted(set(observation_refs)),
        "feature_refs": sorted(set(feature_refs)), "quarantine_refs": sorted(set(quarantine_refs)),
        "observation_count": len(observations), "eligible_feature_count": eligible,
        "as_of": timestamp(as_of) if as_of else None,
    }
    manifest["manifest_ref"] = store.write("manifests", manifest)
    return manifest
