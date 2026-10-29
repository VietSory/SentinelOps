"""Conservative, bounded correlation; structural candidates are not diagnoses."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.telemetry.core import stable_id, timestamp


def topology_paths(topology: dict) -> dict[str, dict[str, list[str]]]:
    if not isinstance(topology, dict) or set(topology) != {"version", "dependencies"} or type(topology["version"]) is not int or topology["version"] < 1:
        raise ValueError("unsupported topology snapshot")
    edges = topology["dependencies"]
    if not isinstance(edges, dict) or not 1 <= len(edges) <= 100:
        raise ValueError("topology must have a bounded component map")
    for node, dependencies in edges.items():
        if not isinstance(node, str) or not node or not isinstance(dependencies, list) or any(not isinstance(dep, str) or dep not in edges for dep in dependencies) or len(set(dependencies)) != len(dependencies):
            raise ValueError("topology contains missing/duplicate/invalid components")
    paths = {}

    def walk(node, stack):
        if node in stack:
            raise ValueError("topology dependencies contain a cycle")
        if node not in paths:
            result = {node: [node]}
            for dep in sorted(edges[node]):
                for target, path in walk(dep, stack | {node}).items():
                    candidate = [node, *path]
                    if target not in result or (len(candidate), candidate) < (len(result[target]), result[target]):
                        result[target] = candidate
            paths[node] = result
        return paths[node]

    for node in sorted(edges):
        walk(node, set())
    return paths


def opening_clusters(openings: list[dict]) -> list[list[dict]]:
    """Partition already validated opening facts without mutating caller order."""
    clusters = []
    for signal in sorted(openings, key=lambda s: (utc_time(s["episode_start_at"]), s["service"], s["signal_id"])):
        if (not clusters or (utc_time(signal["episode_start_at"]) - utc_time(clusters[-1][0]["episode_start_at"])).total_seconds() > 120
            or signal["service"] in {s["service"] for s in clusters[-1]}):
            clusters.append([])
        clusters[-1].append(signal)
    return clusters


def _group_openings(openings: list[dict], paths: dict, topology_sha: str, source_scope_sha256: str) -> list[dict]:
    groups = []
    for cluster in opening_clusters(openings):
        by_service = {s["service"]: s for s in cluster}
        services = set(by_service)
        roots = {service for service in services if not (services - {service}) & paths[service].keys()}
        assignments = {root: [] for root in roots}
        ambiguous = []
        for signal in cluster:
            candidates = sorted(roots & paths[signal["service"]].keys())
            if len(candidates) == 1:
                assignments[candidates[0]].append(signal)
            else:
                ambiguous.append((signal, candidates))
        members = [(group, [root], "dependency-path" if len(group) > 1 else "single-service")
                   for root, group in sorted(assignments.items())]
        members.extend(([signal], candidates, "ambiguous-shared-consumer") for signal, candidates in ambiguous)
        for group, candidates, reason in members:
            support = []
            for signal in group:
                for root in candidates:
                    if root != signal["service"]:
                        support.append({"consumer": signal["service"], "dependency": root,
                            "path": paths[signal["service"]][root], "candidate_signal_id": by_service[root]["signal_id"]})
            onsets = sorted(utc_time(s["episode_start_at"]) for s in group)
            item = {"signal_ids": sorted(s["signal_id"] for s in group),
                "affected_services": sorted(s["service"] for s in group),
                "first_onset_at": timestamp(onsets[0]), "last_onset_at": timestamp(onsets[-1]),
                "structural_candidates": candidates, "grouping_reason": reason,
                "dependency_support": sorted(support, key=lambda p: (p["consumer"], p["dependency"])),
                "topology_sha256": topology_sha, "source_scope_sha256": source_scope_sha256}
            item["group_id"] = stable_id(item)
            groups.append(item)
    return sorted(groups, key=lambda g: (g["first_onset_at"], g["group_id"]))


def correlate_health(signals: list[dict], topology: dict, *, as_of: datetime, source_scope_sha256: str) -> dict:
    """Group opening events within a fixed 120s onset span, before RCA scoring.

    Historical/replay grouping only. Future/non-opening signals are counted,
    not converted into incidents. Shared consumers do not connect sibling roots.
    """
    cutoff = timestamp(as_of)
    if not isinstance(source_scope_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_scope_sha256):
        raise ValueError("correlation requires an explicit verified source scope identity")
    if len(signals) > 100000:
        raise ValueError("correlation exceeds its bounded signal budget")
    paths = topology_paths(topology)
    unique, episode_keys, source_keys = {}, {}, set()
    duplicates, future, non_opening = 0, 0, 0
    openings = []
    for signal in signals:
        validate_payload("health-signal.schema.json", signal)
        key = signal["signal_id"]
        if key in unique:
            duplicates += 1
            continue
        unique[key] = signal
        source_keys.add((signal["environment"], signal["source_profile"], signal["policy_sha256"]))
        if signal["service"] not in paths:
            raise ValueError("signal component is missing from the topology")
        if utc_time(signal["evaluated_at"]) > as_of:
            future += 1
            continue
        if signal["event"] != "incident_opened":
            non_opening += 1
            continue
        identity = (signal["service"], utc_time(signal["episode_start_at"]))
        if identity in episode_keys:
            raise ValueError("conflicting decisions for the same source episode")
        episode_keys[identity] = key
        openings.append(signal)
    if len(source_keys) > 1:
        raise ValueError("correlation cannot mix environment/source/health-policy identities")
    topology_sha = stable_id(topology)
    groups = _group_openings(openings, paths, topology_sha, source_scope_sha256)
    source = next(iter(source_keys), None)
    report = {"schema_version": "1.0", "correlation_version": "slo-topology/v1",
        "mode": "event-time-analytical", "as_of": cutoff, "window_seconds": 120, "source_scope_sha256": source_scope_sha256,
        "topology_sha256": topology_sha, "source_identity": list(source) if source else None,
        "topology_snapshot": deepcopy(topology),
        "input_signal_ids": sorted(unique), "opening_signal_ids": sorted(s["signal_id"] for s in openings),
        "opening_facts": [{key: s[key] for key in ("signal_id", "service", "episode_start_at", "evaluated_at")}
                          for s in sorted(openings, key=lambda s: s["signal_id"])],
        "duplicate_count": duplicates, "future_count": future, "non_opening_count": non_opening,
        "groups": groups,
        "historical_emissions_verified": False, "rca_evaluated": False, "actions_allowed": False}
    report["result_id"] = stable_id({k: v for k, v in report.items() if k != "duplicate_count"})
    validate_payload("correlation-result.schema.json", report)
    return report


def validate_result(result: dict) -> None:
    if result["result_id"] != stable_id({k: v for k, v in result.items() if k not in ("result_id", "duplicate_count")}):
        raise ValueError("correlation result identity mismatch")
    utc_time(result["as_of"])
    paths = topology_paths(result["topology_snapshot"])
    if stable_id(result["topology_snapshot"]) != result["topology_sha256"]:
        raise ValueError("correlation topology snapshot checksum differs")
    inputs, openings = set(result["input_signal_ids"]), set(result["opening_signal_ids"])
    if bool(inputs) != (result["source_identity"] is not None):
        raise ValueError("correlation source identity differs from its input scope")
    if not openings <= inputs or len(openings) + result["future_count"] + result["non_opening_count"] != len(inputs):
        raise ValueError("correlation source accounting is incomplete")
    facts = {fact["signal_id"]: fact for fact in result["opening_facts"]}
    if set(facts) != openings or len(facts) != len(result["opening_facts"]):
        raise ValueError("correlation episode facts do not resolve opening signals")
    for fact in facts.values():
        if not utc_time(fact["episode_start_at"]) < utc_time(fact["evaluated_at"]) <= utc_time(result["as_of"]):
            raise ValueError("correlation episode facts exceed the decision cutoff")
    if len({(f["service"], utc_time(f["episode_start_at"])) for f in facts.values()}) != len(facts):
        raise ValueError("duplicate logical episode facts")
    if any(f["service"] not in paths for f in facts.values()):
        raise ValueError("episode facts reference unknown topology components")
    expected_groups = _group_openings(list(facts.values()), paths, result["topology_sha256"], result["source_scope_sha256"])
    if result["groups"] != expected_groups:
        raise ValueError("correlation groups differ from the conservative episode grouping rule")
    assigned = []
    for group in result["groups"]:
        if group["group_id"] != stable_id({k: v for k, v in group.items() if k != "group_id"}):
            raise ValueError("correlation group identity mismatch")
        if group["topology_sha256"] != result["topology_sha256"]:
            raise ValueError("correlation topology identity differs")
        start, end = utc_time(group["first_onset_at"]), utc_time(group["last_onset_at"])
        if not 0 <= (end - start).total_seconds() <= 120 or end > utc_time(result["as_of"]):
            raise ValueError("correlation group exceeds its bounded temporal span")
        if len(group["signal_ids"]) != len(group["affected_services"]):
            raise ValueError("correlation service membership differs from signals")
        if not set(group["signal_ids"]) <= openings or sorted(facts[s]["service"] for s in group["signal_ids"]) != group["affected_services"]:
            raise ValueError("correlation service membership differs from episode facts")
        onsets = sorted(utc_time(facts[s]["episode_start_at"]) for s in group["signal_ids"])
        if (start, end) != (onsets[0], onsets[-1]):
            raise ValueError("correlation onset bounds differ from episode facts")
        for support in group["dependency_support"]:
            if (support["candidate_signal_id"] not in openings or support["consumer"] not in group["affected_services"]
                or support["dependency"] not in group["structural_candidates"]
                or facts[support["candidate_signal_id"]]["service"] != support["dependency"]
                or paths[support["consumer"]].get(support["dependency"]) != support["path"]):
                raise ValueError("correlation dependency evidence does not resolve")
        assigned.extend(group["signal_ids"])
    if len(set(assigned)) != len(assigned) or set(assigned) != openings:
        raise ValueError("opening episodes must belong to exactly one group")
