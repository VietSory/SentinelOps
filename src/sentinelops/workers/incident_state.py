"""Temporal state for the continuous incident consumer, before source I/O/writes.

The caller must authenticate each accepted batch against its PostgreSQL ledger
and replay its raw health lineage first. Typed inputs alone do not prove that.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.incidents.correlation import correlate_health, opening_clusters, topology_paths
from sentinelops.incidents.health import validate_policy
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp


def reference(kind: str, body: dict) -> str:
    return f"artifact://{kind}/{stable_id(body)}.json"


def advance_groups(previous: dict | None, batch: dict, signal_objects: list[tuple[str, dict]],
                   topology: dict, policy: dict) -> tuple[dict, list[dict]]:
    """Close onset clusters only after a complete accepted input watermark.

    An opening's end = onset + entry_buckets*60. A cluster may receive another
    onset up to first_onset+120 inclusive. Processing through that last possible
    opening end makes membership final; source silence or wall time cannot.
    """
    validate_policy(policy)
    topology_paths(topology)
    identity = batch["identity"]
    if (batch["worker_version"] != "continuous-health/v1" or stable_id(identity) != batch["stream_id"]
        or identity["policy_sha256"] != stable_id(policy)):
        raise ValueError("incident input has a different health source/policy identity")
    end, evaluated = utc_time(batch["bucket_end"]), utc_time(batch["evaluated_at"])
    if end.timestamp() % 60 or evaluated < end:
        raise ValueError("incident input needs a closed aligned actual health decision")
    current, objects = {}, {}
    for ref, signal in signal_objects:
        validate_payload("health-signal.schema.json", signal)
        if (ref != reference("signals", signal) or signal["service"] in current
            or (signal["environment"], signal["source_profile"], signal["policy_sha256"],
                signal["bucket_end"], signal["evaluated_at"]) !=
               (identity["environment"], identity["source_profile"], identity["policy_sha256"],
                batch["bucket_end"], batch["evaluated_at"])):
            raise ValueError("incident input mixes signal identities or decisions")
        current[signal["service"]] = signal
        objects[signal["signal_id"]] = {"signal_ref": ref, "signal": signal}
    if (set(current) != set(BUSINESS_ROUTES) or len(signal_objects) != len(BUSINESS_ROUTES)
        or batch["signal_refs"] != sorted(ref for ref, _ in signal_objects)):
        raise ValueError("incident input omits or duplicates a business-service decision")
    source = {"health_stream_id": batch["stream_id"], "health_identity": identity,
              "topology_sha256": stable_id(topology)}
    batch_ref = reference("manifests", batch)
    pending = []
    if previous is not None:
        if previous["state_version"] != "continuous-correlation-state/v1" or previous["source"] != source:
            raise ValueError("incident checkpoint cannot change source, health policy or topology")
        if previous["last_batch_ref"] == batch_ref:
            if previous["last_bucket_end"] != batch["bucket_end"] or previous["service_states"] != current:
                raise ValueError("incident duplicate differs from its accepted checkpoint")
            return deepcopy(previous), []
        if (end - utc_time(previous["last_bucket_end"]) != timedelta(seconds=60)
            or batch["previous_batch_ref"] != previous["last_batch_ref"]):
            raise ValueError("incident cursor skips or rewrites an accepted health bucket")
        prior = previous["service_states"]
        pending = deepcopy(previous["pending_openings"])
    else:
        if batch["previous_batch_ref"] is not None:
            raise ValueError("incident consumer must start at the stream's first accepted bucket")
        prior = {}
    for service, signal in current.items():
        old = prior.get(service)
        if (signal["previous_signal_id"] != (old["signal_id"] if old else None)
            or old and utc_time(signal["evaluated_at"]) < utc_time(old["evaluated_at"])):
            raise ValueError("incident input breaks its actual health signal chain")
        if signal["event"] == "incident_opened":
            if old and old["incident_active"]:
                raise ValueError("an active source episode cannot open again")
            if end != utc_time(signal["episode_start_at"]) + timedelta(seconds=policy["entry_buckets"] * 60):
                raise ValueError("opening confirmation differs from the fixed source persistence")
            pending.append(objects[signal["signal_id"]])
    if len(pending) > 10000:
        raise ValueError("incident pending-opening budget exceeded; do not discard input")
    lookup, episodes = {}, set()
    for item in pending:
        signal = item["signal"]
        validate_payload("health-signal.schema.json", signal)
        if (signal["event"] != "incident_opened" or item["signal_ref"] != reference("signals", signal)
            or signal["signal_id"] in lookup or utc_time(signal["bucket_end"]) > end
            or utc_time(signal["evaluated_at"]) > evaluated
            or (signal["service"], signal["episode_start_at"]) in episodes
            or (signal["environment"], signal["source_profile"], signal["policy_sha256"]) !=
               (identity["environment"], identity["source_profile"], identity["policy_sha256"])):
            raise ValueError("incident pending state mixes or duplicates source openings")
        lookup[signal["signal_id"]] = item
        episodes.add((signal["service"], signal["episode_start_at"]))
    closed, consumed = [], set()
    for cluster in opening_clusters([item["signal"] for item in pending]):
        first = utc_time(cluster[0]["episode_start_at"])
        horizon = first + timedelta(seconds=120 + policy["entry_buckets"] * 60)
        if end < horizon:
            continue
        if end != horizon:
            raise ValueError("incident checkpoint retained an already finalizable cluster")
        scope = {"scope_version": "continuous-episode-scope/v1", "health_stream_id": batch["stream_id"],
                 "health_policy_sha256": identity["policy_sha256"], "topology_sha256": stable_id(topology),
                 "first_onset_at": timestamp(first), "closing_bucket_end": batch["bucket_end"],
                 "closing_batch_ref": batch_ref,
                 "opening_signal_refs": sorted(lookup[s["signal_id"]]["signal_ref"] for s in cluster)}
        correlation = correlate_health(cluster, topology, as_of=evaluated, source_scope_sha256=stable_id(scope))
        closed.append({"scope": scope, "scope_ref": reference("manifests", scope), "correlation": correlation,
                       "opening_objects": [deepcopy(lookup[s["signal_id"]]) for s in cluster]})
        consumed.update(s["signal_id"] for s in cluster)
    state = {"state_version": "continuous-correlation-state/v1", "source": deepcopy(source),
             "last_bucket_end": batch["bucket_end"], "last_batch_ref": batch_ref,
             "service_states": deepcopy(current),
             "pending_openings": [deepcopy(item) for item in pending if item["signal"]["signal_id"] not in consumed]}
    return state, closed
