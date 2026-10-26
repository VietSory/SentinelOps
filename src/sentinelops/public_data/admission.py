"""Native evidence admission and conservative session groups, before ML splits."""
from __future__ import annotations

from collections import Counter, defaultdict
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

from sentinelops.public_data.cohort import load_cohort_manifest, read_bundle
from sentinelops.public_data.gamma import METRICS, canonical_bytes
from sentinelops.public_data.phases import parse_phases
from sentinelops.public_data.resources import validate_series


def validate_admission_profile(profile: dict) -> None:
    fixed = {"profile_version": "gamma-native-admission/v1", "namespace": "social-network",
        "service_label": "app-name", "node_pattern": "userv[0-9]+",
        "phase_pairing": "strict-kind-measure-closed-intervals", "clock_unit": "unix-seconds",
        "clock_guard_seconds": 1, "maximum_capture_span_seconds": 7200, "session_purge_seconds": 300,
        "grouping": ["observed-utc-day-with-purge", "directory-replicate-family"],
        "training_ready": False, "activation_allowed": False}
    if profile != fixed or profile["training_ready"] is not False or profile["activation_allowed"] is not False:
        raise ValueError("native admission profile differs from its versioned rules")


def parse_pod(text: str, profile: dict) -> dict:
    fields = defaultdict(list)
    labels = []
    in_labels = False
    for line in text.splitlines():
        match = re.fullmatch(r"(Name|Namespace|Node|Start Time|Status|Labels):\s*(.*)", line)
        if match:
            key, value = match.groups()
            fields[key].append(value.strip())
            in_labels = key == "Labels"
            if in_labels:
                labels.append(value.strip())
        elif in_labels and line[:1].isspace():
            labels.append(line.strip())
        else:
            in_labels = False
    required = ("Name", "Namespace", "Node", "Start Time", "Status", "Labels")
    if any(len(fields[key]) != 1 or not fields[key][0] for key in required):
        raise ValueError("pod description must identify one complete native pod")
    if fields["Namespace"][0] != profile["namespace"]:
        raise ValueError("pod namespace differs from native application")
    prefix = profile["service_label"] + "="
    identities = [value.removeprefix(prefix) for value in labels if value.startswith(prefix)]
    if len(identities) != 1 or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", identities[0]):
        raise ValueError("pod lacks a unique native app-name identity")
    node = fields["Node"][0].split("/", 1)[0]
    if not re.fullmatch(profile["node_pattern"], node):
        raise ValueError("pod hosting node is unavailable or unrecognized")
    try:
        started = parsedate_to_datetime(fields["Start Time"][0])
    except (TypeError, ValueError) as exc:
        raise ValueError("pod start time is invalid") from exc
    if started.tzinfo is None:
        raise ValueError("pod start time lacks timezone")
    return {"pod": fields["Name"][0], "entity": identities[0], "node": node,
            "started_at": started.timestamp(), "status": fields["Status"][0]}


def phase_targets(args: dict, profile: dict) -> dict[str, tuple[str, str | None]]:
    if not isinstance(args, dict):
        raise ValueError("native args must be an object")
    kinds = {"cpu": ("cpu", "cpu"), "mem": ("memory", "memory"),
             "net": ("network", "network"), "io": ("io", "io")}
    mixed = any(key + "_bottlenecked_nodes" in args for key in kinds)
    groups = []
    if mixed:
        if args.get("bottlenecked_nodes") not in (None, []):
            raise ValueError("conflicting flat and mixed interference targets")
        for key, (prefix, kind) in kinds.items():
            nodes = args.get(key + "_bottlenecked_nodes")
            groups.append((prefix + "_", kind, [] if nodes is None else nodes))
    else:
        kind = args.get("bottleneck_type")
        if kind is not None:
            if not isinstance(kind, str) or kind.lower() not in {"cpu", "memory", "network", "io"}:
                raise ValueError("unsupported declared native interference kind")
            kind = kind.lower()
        groups.append(("", kind, args.get("bottlenecked_nodes")))
    result = {}
    for prefix, kind, nodes in groups:
        if (not isinstance(nodes, list) or any(not isinstance(node, str) or not re.fullmatch(profile["node_pattern"], node) for node in nodes)
            or len(set(nodes)) != len(nodes)):
            raise ValueError("native targets must be unique VM lists")
        for node in nodes:
            result[prefix + node + "_phases"] = node, kind
    if not result:
        raise ValueError("no declared interference targets; absence is not certified healthy")
    return result


def admit_case(experiment: str, content: dict[str, bytes], profile: dict) -> dict:
    validate_admission_profile(profile)
    prefix = f"raw_dataset/{experiment}/"
    reasons, warnings = [], []
    resources = defaultdict(dict)
    resource_errors = defaultdict(list)
    clocks = []
    for name, blob in sorted(content.items()):
        if not name.startswith(prefix + "prom_metrics/"):
            continue
        native = name.removeprefix(prefix + "prom_metrics/")
        entity, separator, suffix = native.partition("_container_")
        metric = "container_" + suffix
        if not separator or metric not in METRICS or metric in resources[entity]:
            reasons.append("invalid-native-resource-identity")
            continue
        try:
            values = validate_series(json.loads(blob)) if blob else []
            if not values or values[0][0] < 1e9 or values[-1][0] >= 1e10:
                raise ValueError("empty or non-Unix-second resource series")
            resources[entity][metric] = {"first": values[0][0], "last": values[-1][0], "rows": len(values)}
            clocks.append((values[0][0], values[-1][0]))
        except (ValueError, TypeError, UnicodeError):
            resource_errors[entity].append("invalid-series:" + metric)
    bounds = {"first": min(point[0] for point in clocks), "last": max(point[1] for point in clocks)} if clocks else None
    if bounds is None:
        reasons.append("missing-valid-resource-clock")
    elif bounds["last"] - bounds["first"] > profile["maximum_capture_span_seconds"]:
        reasons.append("resource-clock-span-exceeds-profile")
        bounds = None
    pods = defaultdict(list)
    pod_errors = []
    for name, blob in sorted(content.items()):
        if name.startswith(prefix + "pods_describe/"):
            try:
                item = parse_pod(blob.decode(), profile)
                item["source_path"] = name
                pods[item["entity"]].append(item)
            except (ValueError, TypeError, UnicodeError):
                pod_errors.append(name)
    if not pods:
        reasons.append("missing-valid-native-placement")
    if pod_errors:
        reasons.append("unparseable-pod-descriptions")
    entities, excluded = {}, {}
    for entity in sorted(set(resources) | set(resource_errors)):
        issue = list(resource_errors[entity])
        source = resources[entity]
        if set(source) != set(METRICS):
            issue.append("missing-required-resource-series")
        hosting = pods.get(entity, [])
        if not hosting:
            issue.append("missing-app-name-placement")
        elif len({item["node"] for item in hosting}) != 1:
            issue.append("ambiguous-native-hosting-node")
        elif any(item["status"] != "Running" for item in hosting):
            issue.append("non-running-pod-description")
        if entity == "jaeger":
            issue.append("excluded-monitoring-entity")
        if not issue:
            first, last = max(item["first"] for item in source.values()), min(item["last"] for item in source.values())
            if first >= last or min(item["started_at"] for item in hosting) > first + profile["clock_guard_seconds"]:
                issue.append("placement-does-not-cover-resource-start")
        if issue:
            excluded[entity] = sorted(set(issue))
        else:
            entities[entity] = {"node": hosting[0]["node"], "first": first, "last": last,
                "pod_refs": sorted(item["source_path"] for item in hosting), "series": source}
    events = []
    warmup = None
    try:
        args = json.loads(content.get(prefix + "args.txt", b"null"))
        targets = phase_targets(args, profile)
        declared_warmup = args.get("warm_up_duration")
        if type(declared_warmup) is not int or declared_warmup < 0:
            raise ValueError("native warmup must be declared as nonnegative integer seconds")
        warmup = declared_warmup
        actual = {name.removeprefix(prefix): blob for name, blob in content.items()
                  if name.startswith(prefix) and name.endswith("_phases")}
        if set(actual) != set(targets):
            raise ValueError("phase files do not exactly match declared kind/VM targets")
        known_hosts = {item["node"] for collection in pods.values() for item in collection}
        for name, (node, kind) in sorted(targets.items()):
            if node not in known_hosts:
                raise ValueError("phase VM is absent from native placement observations")
            blob = actual[name]
            parsed = parse_phases(blob.decode(), node=node, source_sha256=hashlib.sha256(blob).hexdigest())
            if kind is not None and any(event["kind"] != kind for event in parsed):
                raise ValueError("recorded phase kind differs from its declared native target")
            events.extend(parsed)
        if bounds is None or any(event["start"] < bounds["first"] - profile["clock_guard_seconds"]
                                 or event["end"] > bounds["last"] + profile["clock_guard_seconds"] for event in events):
            raise ValueError("phase intervals are outside the observed resource clock")
    except (ValueError, TypeError, UnicodeError) as exc:
        reasons.append("native-phase-admission:" + str(exc))
        events = []  # Partial truth must not establish healthy intervals.
    if not entities:
        reasons.append("no-eligible-native-entity")
    if warmup is not None and warmup < 300:
        warnings.append("native-declared-warmup-below-unchanged-resource-profile")
    return {"experiment": experiment, "status": "quarantined" if reasons else "native-evidence-admitted",
            "reasons": sorted(set(reasons)), "warnings": warnings, "resource_clock": bounds,
            "native_warmup_seconds": warmup, "entities": entities, "entity_exclusions": excluded,
            "unparsed_pod_sources": pod_errors,
            "events": sorted(events, key=lambda item: (item["start"], item["event_id"])),
            "training_ready": False, "activation_allowed": False}


def session_groups(cases: list[dict], inspected: set[str], profile: dict) -> list[dict]:
    validate_admission_profile(profile)
    names = [case["experiment"] for case in cases]
    if len(names) != len(set(names)) or not inspected <= set(names):
        raise ValueError("duplicate cases or unknown development-inspected identities")
    parents = {name: name for name in names}
    def root(name):
        while parents[name] != name:
            parents[name] = parents[parents[name]]
            name = parents[name]
        return name
    tokens = {}
    for case in sorted(cases, key=lambda item: item["experiment"]):
        name = case["experiment"]
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise ValueError("case has an invalid native identity")
        family = name.rsplit("_", 1)[0] if re.fullmatch(r"[a-z0-9_]+_[0-9]+", name) else name
        keys = ["family:" + family]
        clock = case["resource_clock"]
        if clock:
            if (not all(math.isfinite(clock[key]) for key in ("first", "last")) or clock["last"] < clock["first"]
                or clock["last"] - clock["first"] > profile["maximum_capture_span_seconds"]):
                raise ValueError("invalid clock span for grouping")
            begin = math.floor((clock["first"] - profile["session_purge_seconds"]) / 86400)
            end = math.floor((clock["last"] + profile["session_purge_seconds"]) / 86400)
            keys.extend("utc-day:" + str(day) for day in range(begin, end + 1))
        for key in keys:
            if key in tokens:
                left, right = sorted((root(name), root(tokens[key])))
                parents[right] = left
            else:
                tokens[key] = name
    members = defaultdict(list)
    by_name = {case["experiment"]: case for case in cases}
    for name in sorted(names):
        members[root(name)].append(name)
    result = []
    for group in members.values():
        observed = sorted(set(group) & inspected)
        unknown_clock = [name for name in group if by_name[name]["resource_clock"] is None]
        admitted = sum(by_name[name]["status"] == "native-evidence-admitted" for name in group)
        identity = {"profile": profile, "members": [{"experiment": name, "clock": by_name[name]["resource_clock"],
                    "source_bundle_sha256": by_name[name].get("source_bundle_sha256")} for name in group]}
        result.append({"group_id": hashlib.sha256(canonical_bytes(identity)).hexdigest(),
                       "experiments": group, "development_inspected": observed,
                       "unknown_clock_cases": unknown_clock,
                       "final_holdout_eligible": not observed and not unknown_clock and admitted > 0,
                       "admitted_cases": admitted})
    return sorted(result, key=lambda item: item["experiments"])


def audit_cohort_admission(manifest_path: Path, scope_path: Path, tree_path: Path, profile_path: Path,
                           inspections_path: Path, output: Path, *, project: Path | None = None) -> dict:
    commit = None
    if project is not None:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=project, text=True).strip():
            raise ValueError("commit native admission rules before freezing source evidence")
    scope, tree, profile, inspections = [json.loads(path.read_text()) for path in
                                        (scope_path, tree_path, profile_path, inspections_path)]
    validate_admission_profile(profile)
    inspected_names = inspections.get("holdout_excluded_experiments")
    if (inspections.get("record_version") != "gamma-development-inspections/v1"
        or inspections.get("source_scope_sha256") != tree["scope_sha256"]
        or inspections.get("group_policy") != "exclude-entire-connected-session-group-from-final-holdout"
        or not isinstance(inspected_names, list) or not inspected_names
        or any(not isinstance(name, str) for name in inspected_names)
        or len(set(inspected_names)) != len(inspected_names)):
        raise ValueError("development inspection record differs from frozen cohort scope")
    manifest, bundles = load_cohort_manifest(manifest_path, scope, expected_tree=tree)
    cases = []
    for experiment in scope["experiments"]:
        case = admit_case(experiment, read_bundle(bundles[experiment]), profile)
        case["source_bundle_sha256"] = bundles[experiment]["sha256"]
        cases.append(case)
    inspected = set(inspected_names)
    groups = session_groups(cases, inspected, profile)
    payload = {"report_version": "gamma-native-admission/v1", "source_commit": commit,
        "source_manifest_sha256": manifest_path.stem, "scope_sha256": manifest["scope_sha256"],
        "source_file_tree_sha256": tree["source_file_tree_sha256"], "training_data_policy_hash": manifest["training_data_policy_hash"],
        "profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
        "inspections_sha256": hashlib.sha256(inspections_path.read_bytes()).hexdigest(),
        "case_count": len(cases), "status_counts": dict(Counter(case["status"] for case in cases)),
        "reason_counts": dict(Counter(reason for case in cases for reason in case["reasons"])),
        "group_count": len(groups), "candidate_final_holdout_groups": sum(item["final_holdout_eligible"] for item in groups),
        "grouping_is_independence_proof": False, "cases": cases, "groups": groups,
        "training_ready": False, "activation_allowed": False,
        "blocking_reasons": ["native-evidence admission is not operational health or precursor/SLO truth",
            "session groups are conservative split constraints, not a frozen train/calibration/development/holdout assignment",
            "feature coverage and independent reference sufficiency require measurement under the unchanged resource profile",
            "pod snapshots do not prove continuous placement or online arrival-time availability"]}
    blob = canonical_bytes(payload)
    digest = hashlib.sha256(blob).hexdigest()
    output.mkdir(parents=True, exist_ok=False)
    (output / (digest + ".json")).write_bytes(blob)
    return {key: value for key, value in payload.items() if key not in ("cases", "groups")} | {
        "report_sha256": digest, "report_path": str(output / (digest + ".json"))}
