"""Verify transported continuous incident ledgers without PostgreSQL or Docker.

Recompute accepted raw health, grouping, operational RCA and journal projection.
This preserves captured clocks; it does not establish a new live measurement or
authenticate a selected capture without independently supplied snapshot hashes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from sentinelops.contracts import utc_time
from sentinelops.incidents.domain import IncidentWrite, check_transition
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore, KINDS
from sentinelops.workers.health_source import decoded, validate_stream, verify_health_batch
from sentinelops.workers.incident_journal import ACTOR, episode_key
from sentinelops.workers.incident_rca import derive_continuous
from sentinelops.workers.incident_state import advance_groups


MAX_OBJECT_BYTES = 16 * 1024 * 1024
MAX_BATCHES = 10000
STAGES = ["OPEN", "RCA_READY", "ESCALATED", "CLOSED"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def bounded_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        body = stream.read(MAX_OBJECT_BYTES + 1)
    require(len(body) <= MAX_OBJECT_BYTES, "incident replay object exceeds its byte budget")
    return body


def json_object(body: bytes) -> dict:
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "incident replay JSON contains duplicate keys")
            value[key] = item
        return value

    def nonfinite(_):
        raise ValueError("incident replay JSON contains a nonfinite number")

    value = json.loads(body, object_pairs_hook=pairs, parse_constant=nonfinite)
    require(isinstance(value, dict), "incident replay requires a JSON object")
    return value


class ReplayStore(ArtifactStore):
    """Read only byte-bounded, hash-verified objects at relocatable references."""

    def read(self, reference: str) -> dict:
        match = re.fullmatch(r"artifact://([a-z]+)/([0-9a-f]{64})\.json", reference)
        require(match is not None and match[1] in KINDS, "invalid replay artifact reference")
        body = bounded_bytes(self.root / match[1] / (match[2] + ".json"))
        require(hashlib.sha256(body).hexdigest() == match[2], "replay artifact checksum mismatch")
        return json_object(body)


def index_rows(rows: list[dict], key: str) -> dict:
    require(isinstance(rows, list), "incident replay requires a row list")
    require(all(isinstance(row, dict) for row in rows), "incident replay row must be an object")
    result = {row[key]: row for row in rows}
    require(len(result) == len(rows), "duplicate incident replay row identity: " + key)
    return result


def verify_prefix(health, journal, source, store, identity, identifier, topology, health_policy):
    steps = index_rows(journal["incident_consumer_steps"], "bucket_end")
    require(len(steps) == len(health["batches"]), "consumer steps do not cover the accepted health prefix")
    signals = index_rows(health["signals"], "signal_id")
    require(len(signals) == 3 * len(health["batches"]), "accepted health prefix omits service decisions")
    state, previous, closed, views = None, None, {}, {}
    for number, row in enumerate(health["batches"]):
        records = [r for r in health["signals"] if r["bucket_end"] == row["bucket_end"]]
        verified = verify_health_batch(source, health["stream"], row, records,
            health["batches"][max(0, number - 4):number], previous, policy=health_policy)
        state, groups = advance_groups(state, verified["batch"], verified["signal_objects"], topology, health_policy)
        step = steps[row["bucket_end"]]
        require(step["consumer_id"] == identifier and step["health_batch_ref"] == row["manifest_ref"],
                "consumer step differs from its selected accepted source")
        saved = store.read(step["state_ref"])
        require({k: v for k, v in saved.items() if k != "source_ledger_view_ref"} == state,
                "consumer state differs from recomputed raw health/grouping")
        view = store.read(saved["source_ledger_view_ref"])
        require(view["accepted_row"] == row and view["signal_refs"] == verified["batch"]["signal_refs"]
                and view["health_policy"] == health_policy, "source read view differs from its accepted batch")
        require(view["source_stream"]["stream_id"] == identity["health_stream_id"]
                and decoded(view["source_stream"]["identity"]) == health["stream"]["identity"],
                "source read view changes stream identity")
        require(utc_time(view["source_read_at"]) >= utc_time(row["recorded_at"]),
                "source read view predates accepted source receipt")
        views[saved["source_ledger_view_ref"]] = view
        for scope in groups:
            for group in scope["correlation"]["groups"]:
                require(group["group_id"] not in closed, "source group was finalized more than once")
                closed[group["group_id"]] = (scope, group)
        previous = verified["service_states"]
    checkpoint = journal["incident_consumer_checkpoints"]
    require(len(checkpoint) == 1 and checkpoint[0]["consumer_id"] == identifier,
            "consumer checkpoint has a different owner or multiplicity")
    require(checkpoint[0]["last_state_ref"] == steps[state["last_bucket_end"]]["state_ref"]
            and state["last_batch_ref"] == health["stream"]["last_batch_ref"]
            and state["last_bucket_end"] == health["stream"]["last_bucket_end"],
            "consumer checkpoint does not match the final accepted watermark")
    return closed, signals, views


def verify_recoveries(journal, signals, stream_id, identifier, health_policy, store):
    recoveries = index_rows(journal["incident_episode_recoveries"], "episode_key")
    for key, row in recoveries.items():
        raw = signals[row["signal_id"]]
        signal = raw["payload"]
        require(row["consumer_id"] == identifier and raw["stream_id"] == stream_id
                and key == episode_key(stream_id, signal), "recovery changes source/episode identity")
        require(store.read(row["signal_ref"]) == signal and signal["event"] == "recovery_confirmed"
                and signal["availability"] == "ELIGIBLE" and not signal["incident_active"]
                and signal["clear_streak"] == health_policy["recovery_buckets"],
                "recovery is not the accepted natural source decision")
        require((row["service"], row["episode_start_at"], row["bucket_end"]) ==
                (signal["service"], signal["episode_start_at"], signal["bucket_end"]),
                "recovery journal fields differ from their raw source")
    return recoveries


def verify_commands(job, intent, scope, group, tables, derived, recoveries, views, store, stream_id):
    rca = derived["rca"]
    commands = sorted(index_rows(tables["incident_commands"], "command_id").values(), key=lambda r: r["expected_revision"])
    versions = sorted(index_rows(tables["incident_versions"], "revision").values(), key=lambda r: r["revision"])
    receipts = index_rows(tables["incident_command_receipts"], "command_id")
    events = index_rows(tables["incident_events"], "revision")
    outbox = index_rows(tables["outbox"], "event_id")
    require(len(commands) == len(versions) == len(receipts) == len(events) == len(outbox) == 4,
            "complete native lifecycle requires four commands/revisions/receipts/audit/outbox rows")
    prior_stage, prior_payload, stages = None, None, []
    for number, (row, version) in enumerate(zip(commands, versions)):
        command = IncidentWrite(**store.read(row["command_ref"]))
        command.validate()
        require(command.actor == ACTOR and command.expected_revision == row["expected_revision"] == number
                and row["job_id"] == job["job_id"] and row["incident_id"] == job["incident_id"]
                and row["lifecycle_stage"] == command.lifecycle_stage, "command changes journal identity or lifecycle")
        require(command.idempotency_key == row["command_id"] ==
                stable_id({"job_id": job["job_id"], "expected_revision": number}), "command retry identity differs")
        require(command.fingerprint() == row["request_hash"] == version["request_hash"]
                and version["idempotency_key"] == command.idempotency_key and version["revision"] == number + 1,
                "published revision differs from its immutable command")
        require(version["incident_id"] == job["incident_id"] and version["actor"] == command.actor
                and version["reason"] == command.reason, "published revision changes command attribution")
        require(command.payload == decoded(version["payload"]) and command.payload["service_signals"] == []
                and command.payload["detected_at"] == intent["detected_at"]
                and command.payload["severity"] == derived["severity"], "published payload changes source-derived incident")
        require(decoded(version["context"]) == {**command.context, "lifecycle_stage": command.lifecycle_stage},
                "published context differs from immutable command")
        check_transition(prior_stage, command, prior_payload)
        require(utc_time(version["recorded_at"]) >= utc_time(job["detected_at"]), "IER receipt predates frozen intent")
        for reference in command.context["source_refs"]:
            store.read(reference)
        for fact in command.payload["evidence"]:
            store.read(fact["source_ref"])
        receipt, event = receipts[row["command_id"]], events[number + 1]
        require(receipt["incident_id"] == job["incident_id"] and receipt["revision"] == number + 1
                and receipt["expected_revision"] == number, "command receipt points to another revision")
        require(event["lifecycle_stage"] == command.lifecycle_stage and event["incident_id"] == job["incident_id"]
                and event["actor"] == command.actor and event["reason"] == command.reason
                and event["event_type"] == "incident.revised", "audit event differs from immutable command")
        queued = outbox[event["event_id"]]
        require(queued["topic"] == "incident.revised" and decoded(queued["body"]) == {
            "incident_id": job["incident_id"], "revision": number + 1,
            "lifecycle_stage": command.lifecycle_stage, "request_hash": row["request_hash"]},
            "outbox event differs from its incident revision")
        if number:
            candidates = {c["service"]: c for c in rca["candidates"]}
            ranking = [{key: candidates[s][key] for key in ("service", "score", "evidence_refs")} for s in rca["ranking"]]
            require(command.payload["root_cause_ranking"] == ranking, "IER ranking differs from raw-derived RCA")
            facts = {f["id"]: f for f in command.payload["evidence"]}
            require(all(facts.get(f["id"]) == f for f in rca["evidence"]), "IER omits or changes raw-derived RCA support")
        else:
            require(command.payload["root_cause_ranking"] == [], "OPEN invents a premature ranking")
        if command.lifecycle_stage == "CLOSED":
            require(command.payload["state"] == "recovered" and command.context["availability"] == "ELIGIBLE"
                    and command.context["recovery_source"] == "natural", "closure is not natural source recovery")
            for opening in scope["opening_objects"]:
                signal = opening["signal"]
                if signal["signal_id"] in group["signal_ids"]:
                    recovery = recoveries[episode_key(stream_id, signal)]
                    require(recovery["signal_ref"] in command.context["source_refs"], "closure omits a member recovery")
            selected = [views[r] for r in command.context["source_refs"] if r in views]
            require(bool(selected), "closure omits a verified current source view")
            view = max(selected, key=lambda v: v["accepted_row"]["bucket_end"])
            current = [store.read(ref) for ref in view["signal_refs"]]
            require(all(s["availability"] == "ELIGIBLE" for s in current if s["service"] in group["affected_services"]),
                    "UNKNOWN current source cannot close an incident")
        stages.append(command.lifecycle_stage)
        prior_stage, prior_payload = command.lifecycle_stage, command.payload
    require(stages == STAGES, "native journal does not contain the declared complete lifecycle")
    pointer = tables["incidents"]
    require(len(pointer) == 1 and pointer[0]["incident_id"] == job["incident_id"]
            and pointer[0]["current_revision"] == 4 and pointer[0]["lifecycle_stage"] == "CLOSED",
            "current incident pointer differs from verified history")
    return stages


def replay(root: Path, *, expected_health_snapshot_sha256: str | None = None,
           expected_incident_snapshot_sha256: str | None = None) -> dict:
    """Verify a completed S1/S5 native capture. Optional hashes select trusted bytes."""
    if not __debug__:
        raise ValueError("offline acceptance replay requires assertions enabled")
    for expected in (expected_health_snapshot_sha256, expected_incident_snapshot_sha256):
        require(expected is None or isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected),
                "expected snapshot checksum must be a SHA256 hex digest")
    try:
        return _replay(Path(root), expected_health_snapshot_sha256, expected_incident_snapshot_sha256)
    except (KeyError, TypeError, IndexError, RecursionError) as exc:
        raise ValueError("captured incident ledger has malformed or missing required fields") from exc


def _replay(root, expected_health, expected_incident):
    source, store = ReplayStore(root / "worker"), ReplayStore(root / "incident-consumer/objects")
    health_bytes = bounded_bytes(root / "database-snapshot.json")
    journal_bytes = bounded_bytes(root / "incident-consumer/database-snapshot.json")
    health_hash, journal_hash = hashlib.sha256(health_bytes).hexdigest(), hashlib.sha256(journal_bytes).hexdigest()
    require(expected_health is None or health_hash == expected_health, "health ledger differs from selected snapshot checksum")
    require(expected_incident is None or journal_hash == expected_incident, "incident ledger differs from selected snapshot checksum")
    health, journal = json_object(health_bytes), json_object(journal_bytes)
    accepted = json_object(bounded_bytes(root / "incident-consumer/acceptance.json"))
    require(accepted["status"] == "PASS" and journal_hash == accepted["database_snapshot_sha256"],
            "incident snapshot differs from its acceptance checksum")
    consumer, jobs = journal["consumer"], journal["jobs"]
    identity, identifier = decoded(consumer["identity"]), consumer["consumer_id"]
    health_identity = health["stream"]["identity"]
    require(identity["health_stream_id"] == health["stream"]["stream_id"] == accepted["stream_id"] == consumer["health_stream_id"],
            "incident snapshot changes accepted health stream")
    require(identifier == accepted["consumer_id"] == stable_id({
        "consumer_version": "continuous-incident/v1", "health_stream_id": identity["health_stream_id"]}),
        "incident snapshot changes consumer identity")
    require(health["snapshot_version"] == "health-ledger-snapshot/v1" and 0 < len(health["batches"]) <= MAX_BATCHES,
            "unsupported or unbounded accepted health snapshot")
    require(0 < len(index_rows(jobs, "job_id")) <= MAX_BATCHES, "completed capture must contain unique bounded jobs")
    first = store.read(jobs[0]["intent_ref"])
    policy, topology = first["rca_policy"], store.read(first["correlation_ref"])["topology_snapshot"]
    require(identity["rca_policy_sha256"] == stable_id(policy) and identity["topology_sha256"] == stable_id(topology),
            "incident source changes RCA policy or topology")
    first_state = store.read(journal["incident_consumer_steps"][0]["state_ref"])
    health_policy = store.read(first_state["source_ledger_view_ref"])["health_policy"]
    require(identity["health_policy_sha256"] == stable_id(health_policy), "incident source changes health policy")
    validate_stream(health["stream"], health_policy)
    closed, signals, views = verify_prefix(health, journal, source, store, identity, identifier, topology, health_policy)
    require(set(closed) == {j["group_id"] for j in jobs}, "journal omits, duplicates or adds a finalized source group")
    recoveries = verify_recoveries(journal, signals, identity["health_stream_id"], identifier, health_policy, store)
    results = []
    for job in jobs:
        require(job["consumer_id"] == identifier and job["job_id"] == stable_id({
            "consumer_id": identifier, "group_id": job["group_id"]}) and job["incident_id"] == "INC-" + job["group_id"],
            "job identity differs from its finalized source group")
        scope, group = closed[job["group_id"]]
        intent, tables = store.read(job["intent_ref"]), journal[job["job_id"]]
        require(intent["consumer_id"] == identifier and intent["job_id"] == job["job_id"]
                and intent["incident_id"] == job["incident_id"] and intent["rca_policy"] == policy
                and intent["detected_at"] == job["detected_at"], "frozen intent differs from accepted job")
        require(intent["opening_objects"] == scope["opening_objects"] and intent["scope_ref"] == scope["scope_ref"]
                and store.read(intent["correlation_ref"]) == scope["correlation"]
                and intent["group_id"] == group["group_id"]
                and job["closing_bucket_end"] == store.read(intent["scope_ref"])["closing_bucket_end"],
                "frozen intent differs from recomputed source scope")
        rows = tables["incident_job_evidence"]
        require(len(rows) == 1 and rows[0]["job_id"] == job["job_id"], "job needs one immutable operational capture")
        evidence = rows[0]
        derived = derive_continuous(store, intent_ref=job["intent_ref"], intent=intent, closed=scope,
            capture_ref=evidence["capture_ref"], profile=identity["evidence_profile"],
            health_identity=health_identity, health_policy=health_policy)
        rca = derived["rca"]
        require(store.read(evidence["result_ref"]) == rca and evidence["analyzed_at"] == rca["as_of"]
                and rca["actions_allowed"] is False, "stored RCA differs from raw operational evidence")
        stages = verify_commands(job, intent, scope, group, tables, derived, recoveries, views, store, identity["health_stream_id"])
        results.append({"incident_id": job["incident_id"], "group_id": job["group_id"],
            "members": group["affected_services"], "grouping_reason": group["grouping_reason"],
            "structural_candidates": group["structural_candidates"], "abstention_reasons": rca["abstention_reasons"],
            "stages": stages, "rca_result_ref": evidence["result_ref"]})
    metadata = root / "acceptance.json"
    source_commit = json_object(bounded_bytes(metadata)).get("source_commit") if metadata.exists() else None
    require(source_commit is None or isinstance(source_commit, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source_commit),
            "capture source commit is malformed")
    return {"status": "PASS", "verifier_version": "continuous-incident-offline/v1",
        "mode": "offline-transported-continuous-incident-integrity-replay-not-new-live-evidence",
        "data_source_commit": source_commit, "batches": len(health["batches"]), "signals": len(health["signals"]),
        "jobs": results, "health_snapshot_sha256": health_hash, "incident_snapshot_sha256": journal_hash,
        "actual_receipt_clocks_preserved": True, "externally_bound_health_snapshot": expected_health is not None,
        "externally_bound_incident_snapshot": expected_incident is not None,
        "new_historical_alert_or_causality_claim": False, "actions_allowed": False}
