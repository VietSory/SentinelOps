"""Verify raw telemetry and replay objective health at fixed decision cutoffs."""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.incidents.health import advance_health, validate_policy
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.store import ArtifactStore


def replay_health_manifest(source: ArtifactStore, manifest_ref: str, policy: dict, output: Path,
                           *, project: Path | None = None, maximum_buckets: int = 10000) -> dict:
    """Replay captured availability; evaluated_at is not a historical emission.

    No experiment/run/truth metadata is needed or read. The source store is not
    modified; normalization parity runs in a disposable store.
    """
    validate_policy(policy)
    commit = None
    if project is not None:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=project, text=True).strip():
            raise ValueError("commit health implementation/policy before real-source replay")
    manifest = source.read(manifest_ref)
    normalization_ref = manifest.get("normalization_manifest_ref", manifest_ref)
    normalization = source.read(normalization_ref)
    if any(manifest.get(key) != value for key, value in normalization.items()):
        raise ValueError("source manifest differs from its normalization revision")
    raw_refs = normalization["raw_refs"]
    if not raw_refs or len(raw_refs) > maximum_buckets * len(BUSINESS_ROUTES) or len(set(raw_refs)) != len(raw_refs):
        raise ValueError("missing/duplicate/oversized raw telemetry scope")
    captures = [source.read(ref) for ref in raw_refs]
    allowed = {"capture_version", "source_profile", "service", "bucket_end", "collected_at", "queries"}
    if any(set(capture) != allowed or capture["capture_version"] != "1.0" for capture in captures):
        raise ValueError("unsupported raw capture fields; operational input must exclude control metadata")
    ends = []
    for capture in captures:
        end = utc_time(capture["bucket_end"])
        if (end.timestamp() % 60 or capture["service"] not in BUSINESS_ROUTES
            or capture["source_profile"] != normalization["source_profile"]):
            raise ValueError("raw capture scope has an unsupported source/service/clock")
        ends.append(end)
    with TemporaryDirectory(prefix="sentinelops-health-source-") as temporary:
        replayed = process_captures(captures, ArtifactStore(temporary), environment=normalization["environment"])
    if replayed.pop("manifest_ref") != normalization_ref or replayed != normalization:
        raise ValueError("raw telemetry does not reproduce the normalization manifest")
    observations = {}
    for reference in normalization["observation_refs"]:
        observation = source.read(reference)
        validate_payload("observation-envelope.schema.json", observation)
        key = (observation["service"], utc_time(observation["bucket_end"]))
        if key in observations and observations[key] != observation:
            raise ValueError("conflicting observation revisions need explicit resolution")
        observations[key] = observation
    # Raw clocks retain quarantined leading/trailing buckets and whole outages.
    # Bounding by accepted observations would silently shorten the outage.
    start, last = min(ends), max(ends)
    bucket_count = int((last - start).total_seconds() // 60) + 1
    if not 1 <= bucket_count <= maximum_buckets:
        raise ValueError("health replay exceeds its bounded bucket horizon")
    output.mkdir(parents=True, exist_ok=False)
    destination = ArtifactStore(output)
    states, signal_refs, events, availability, health = {}, [], Counter(), Counter(), Counter()
    for index in range(bucket_count):
        end = start + timedelta(seconds=60 * index)
        as_of = end + timedelta(seconds=policy["maximum_lag_seconds"])
        for service in sorted(BUSINESS_ROUTES):
            signal = advance_health(states.get(service), observations.get((service, end)), service=service,
                environment=normalization["environment"], source_profile=normalization["source_profile"],
                bucket_end=end, evaluated_at=as_of, policy=policy)
            states[service] = signal
            signal_refs.append(destination.write("signals", signal))
            events[service + "/" + signal["event"]] += 1
            availability[service + "/" + signal["availability"]] += 1
            health[service + "/" + str(signal["health"])] += 1
    report = {"report_version": "slo-health-replay/v1", "mode": "availability-aware-replay",
        "source_commit": commit, "source_manifest_ref": manifest_ref, "normalization_manifest_ref": normalization_ref,
        "policy": policy, "policy_sha256": stable_id(policy), "bucket_count": bucket_count,
        "raw_capture_count": len(raw_refs), "source_observation_count": len(observations),
        "source_quarantine_count": len(normalization["quarantine_refs"]),
        "signal_count": len(signal_refs), "signal_refs": signal_refs, "event_counts": dict(events),
        "availability_counts": dict(availability), "health_counts": dict(health),
        "final_states": states, "raw_normalization_parity": True,
        "historical_emission_times_verified": False, "model_outputs_available": False,
        "rca_evaluated": False, "actions_dispatched": False}
    report["report_ref"] = destination.write("manifests", report)
    return report
