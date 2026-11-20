"""Read evidence projections pinned to an existing incident or action."""

from sentinelops.contracts import validate_payload


def rca_coverage(store, snapshot):
    ranking = snapshot["payload"]["root_cause_ranking"]
    if not ranking:
        return {}
    pinned = set(snapshot["context"]["source_refs"])
    references = {
        fact["source_ref"] for fact in snapshot["payload"]["evidence"]
        if fact["type"] == "timeline" and fact["source_ref"] in pinned
        and fact["fact"].startswith("Source-verified continuous RCA hypotheses;")
    }
    if len(references) != 1:
        return {}
    try:
        result = store.read(references.pop())
        validate_payload("rca-result.schema.json", result)
        candidates = {candidate["service"]: candidate for candidate in result["candidates"]}
        expected = [{key: candidates[service][key] for key in ("service", "score", "evidence_refs")}
                    for service in result["ranking"]]
        if not result["abstained"] and ranking == expected:
            return {row["service"]: candidates[row["service"]]["coverage"] for row in ranking}
    except (OSError, KeyError, ValueError, TypeError):
        pass
    return {}


def action_verification(state, store):
    facts = state["facts"]
    telemetry = facts.get("telemetry")
    if state["phase"] != "recovered" or not telemetry or telemetry["status"] != "telemetry_verified":
        return None
    support = telemetry["support"]
    verification = {
        "status": telemetry["status"],
        "verified_at": facts["verification_decided_at"],
        "bucket_ends": [item["bucket_end"] for item in support],
    }
    if store is None:
        return verification
    readings = []
    for item in support:
        try:
            observation = store.read(item["observation_ref"])
            validate_payload("observation-envelope.schema.json", observation)
            if (observation["service"] != telemetry["service"]
                    or observation["environment"] != telemetry["environment"]
                    or observation["bucket_end"] != item["bucket_end"]
                    or observation["quality"]["status"] != "valid"):
                return verification
            payload = observation["payload"]
            validate_payload("telemetry.schema.json", payload)
            readings.append(payload)
        except (OSError, KeyError, ValueError, TypeError):
            return verification
    if readings:
        verification["p95_latency_ms"] = max(item["p95_latency_ms"] for item in readings)
        verification["error_rate"] = max(item["error_rate"] for item in readings)
    return verification
