"""Reproduce accepted continuous decisions without network or a live database."""
from __future__ import annotations

from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.workers.health_source import verify_health_batch
from sentinelops.workers.incident_state import advance_groups
from sentinelops.workers.incidents import evaluate_cluster, incident_commands


def replay_incident_history(store, snapshot, topology, health_policy, rca_policy):
    identity = snapshot["consumer_identity"]
    consumer_id = stable_id(identity)
    if (identity["worker_version"] != "continuous-incidents/v1" or identity["topology_sha256"] != stable_id(topology)
        or identity["health_policy_sha256"] != stable_id(health_policy) or identity["rca_policy_sha256"] != stable_id(rca_policy)
        or identity["health_stream_id"] != snapshot["stream"]["stream_id"]):
        raise ValueError("archived continuous identity differs from selected policies/topology")
    health_state, verified = None, []
    for index, row in enumerate(snapshot["batches"]):
        source = verify_health_batch(store, snapshot["stream"], row,
            [r for r in snapshot["signals"] if r["bucket_end"] == row["bucket_end"]], snapshot["batches"][max(0, index - 4):index],
            health_state, policy=health_policy)
        health_state = source["service_states"]
        verified.append(source)
    state, previous_ref, ids = None, None, set()
    references = snapshot["consumer_commit_refs"]
    if len(set(references)) != len(references) or len(references) > len(verified):
        raise ValueError("archived consumer duplicates or exceeds accepted source history")
    for source, reference in zip(verified, references):
        recorded = store.read(reference)
        if recorded["consumer_id"] != consumer_id or recorded["previous_commit_ref"] != previous_ref:
            raise ValueError("archived consumer commit chain differs")
        state, closed = advance_groups(state, source["batch"], source["signal_objects"], topology, health_policy)
        if state != recorded["state"] or closed != recorded["closed_clusters"] or len(recorded["modalities"]) != len(closed):
            raise ValueError("continuous grouping/state does not reproduce from accepted raw source")
        rows, commands = [], []
        for item, modalities in zip(closed, recorded["modalities"]):
            results = evaluate_cluster(store, item, policy=rca_policy, as_of=utc_time(recorded["processed_at"]),
                                       health_policy=health_policy, modalities=modalities)
            rows.extend(results)
            commands.extend(incident_commands(consumer_id, item, results, recorded["processed_at"]))
        if rows != recorded["results"] or [c.__dict__ for c in commands] != recorded["commands"]:
            raise ValueError("continuous RCA/IER projection does not reproduce at its actual processing clock")
        ids.update(c.payload["incident_id"] for c in commands)
        previous_ref = reference
    return {"replay_version": "continuous-incident-history/v1", "status": "PASS", "consumer_id": consumer_id,
            "verified_health_buckets": len(verified), "verified_consumer_buckets": len(references),
            "remaining_accepted_buckets": len(verified) - len(references), "incidents": len(ids),
            "raw_health_correlation_rca_ier_parity": True, "actions_allowed": False,
            "model_outputs_available": False, "mode": "replay-of-recorded-continuous-runtime"}
