"""Whole-group prospective development replay; no final access or action authority."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path, PurePosixPath

import yaml

from sentinelops.contracts import utc_time
from sentinelops.experiments.local_corpus import PROJECT, KINDS, clean_source, read_binding
from sentinelops.experiments.multifault import post_capture_cutoff
from sentinelops.incidents.correlation_replay import replay_correlation
from sentinelops.incidents.health_replay import replay_health_manifest
from sentinelops.incidents.rca_replay import replay_rca
from sentinelops.ml.local_data import _inside, admitted_local_evaluation_rows
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore

PROTOCOL = {
    "protocol_version": "local-rca-development/v1",
    "evaluation_role": "development-validation",
    "source_profile": "prometheus-increase-60s-5s/v1",
    "rca_policy_sha256": "62ee6f9ed87a1dddcef3ef30cf45e662da887ce0f532a2638c6f4d9d5eab052e",
    "health_policy_sha256": "52df658a1569c82e3cbbc85909e3d5f980137636b09626ec5bdcfa059b768560",
    "topology_sha256": "ef83d6691437734bbec3c8de806e41d30a178b5ed219a1e0f4be0c4f496e87bd",
    "analysis_cutoff": "maximum-operational-receipt-and-correlation-cutoff/v1",
    "group_selection": "earliest-onset-then-group-id/v1",
    "denominator": "all-registered-development-trials/v1",
    "minimum_whole_groups": 5,
    "model_outputs_available": False,
    "final_access_allowed": False,
    "activation_allowed": False,
}
ROOTS = {"healthy": None, "payment-gradual": "payment-service", "inventory-gradual": "inventory-service"}
COMPARISONS = ("full-diagnostic", "full-qualified", "anomaly-only", "topology-time-only")


def load_protocol(project=PROJECT):
    value = json.loads((project / "configs/local_rca_evaluation.json").read_bytes())
    if (value != PROTOCOL or set(value) != set(PROTOCOL)
        or any(type(value[k]) is not type(v) for k, v in PROTOCOL.items())):
        raise ValueError("local RCA development protocol is frozen; final access stays closed")
    return value


def comparison_ranks(rca, group, qualified):
    # Give tied candidates the last position in the tie, independent of names.
    def rank(values):
        return {service: sum(other >= value for other in values.values())
                for service, value in sorted(values.items())}

    if rca is None:
        return {method: {} for method in COMPARISONS}
    candidates = {candidate["service"]: candidate for candidate in rca["candidates"]}
    full = rank({service: candidate["score"] for service, candidate in candidates.items()
                 if candidate["score"] is not None and candidate["score"] > 0})
    anomaly = {service: candidate["components"]["anomaly_strength"]["value"]
               for service, candidate in candidates.items()}
    temporal = {service: candidates[service]["components"]["temporal_precedence"]["value"]
                for service in group["structural_candidates"]}
    topology_time = ({service: len(temporal) for service in sorted(temporal)}
                     if None in temporal.values() else rank(temporal))
    return {"full-diagnostic": full, "full-qualified": full if qualified else {},
            "anomaly-only": rank({service: value for service, value in anomaly.items()
                                  if value is not None and value > 0}),
            "topology-time-only": topology_time}


def summarize_comparisons(trials):
    faults = [trial for trial in trials if trial["expected_intervention_root"] is not None]
    summary = {}
    for method in COMPARISONS:
        ranks = [trial["comparison_ranks"][method] for trial in faults]
        positions = [ranks_by_service.get(trial["expected_intervention_root"])
                     for trial, ranks_by_service in zip(faults, ranks)]
        summary[method] = {
            "fault_trials": len(faults),
            "ranked_fault_trials": sum(bool(rank) for rank in ranks),
            "top1": sum(position == 1 for position in positions) / len(faults) if faults else None,
            "top3": sum(position is not None and position <= 3 for position in positions) / len(faults) if faults else None,
            "mrr": sum(1 / position for position in positions if position is not None) / len(faults) if faults else None,
        }
    return summary


def trial_result(case, run, diagnostic):
    """Evaluate truth only after diagnostics; never choose the most favorable group."""
    expected = ROOTS[case["kind"]]
    if run["root_cause_ground_truth"] != expected:
        raise ValueError("intervention truth differs from its source-admitted trial kind")
    results = diagnostic["results"]
    if any(row["rca"]["actions_allowed"] is not False for row in results):
        raise ValueError("development replay cannot grant action authority")
    groups = [(next(group for group in row["rca"]["correlation"]["groups"]
                    if group["group_id"] == row["group_id"]), row["rca"]) for row in results]
    first_group, first = min(groups, key=lambda entry: (
        utc_time(entry[0]["first_onset_at"]), entry[0]["group_id"]), default=(None, None))
    ranking = first["ranking"] if first else []
    qualified = first is not None and not first["abstained"]
    oracle = case["oracle"]["services"]
    return {
        "run_id": case["run_id"], "group_id": case["group_id"], "kind": case["kind"],
        "source_result_ref": case["source_result_ref"], "oracle_ref": case["oracle_ref"],
        "expected_intervention_root": expected,
        "independent_root_slo_confirmed_at": oracle[expected]["confirmed_at"] if expected else None,
        "diagnostic_group_count": len(groups),
        "selected_group_id": first_group["group_id"] if first_group else None,
        "comparison_ranks": comparison_ranks(first, first_group, qualified),
        "diagnostic_ranking": ranking,
        "abstained": first["abstained"] if first else None,
        "abstention_reasons": first["abstention_reasons"] if first else [],
        "qualified_prediction": qualified,
        "diagnostic_top1_hit": bool(expected and ranking and ranking[0] == expected),
        "diagnostic_top3_hit": bool(expected and expected in ranking[:3]),
        "qualified_top1_hit": bool(expected and qualified and ranking[0] == expected),
        "healthy_trial_with_incident_group": expected is None and bool(groups),
        "rca_report_ref": diagnostic["report_ref"],
    }


def summarize_trials(trials):
    """Descriptive trial counts: missing groups and abstentions remain in denominators."""
    faults = [t for t in trials if t["expected_intervention_root"] is not None]
    healthy = [t for t in trials if t["expected_intervention_root"] is None]
    qualified = [t for t in faults if t["qualified_prediction"]]
    counts = {"whole_groups": len({t["group_id"] for t in trials}), "trials": len(trials),
        "healthy_trials": len(healthy), "fault_trials": len(faults),
        "fault_trials_without_independent_confirmed_slo": sum(t["independent_root_slo_confirmed_at"] is None for t in faults),
        "healthy_trials_with_incident_group": sum(t["healthy_trial_with_incident_group"] for t in healthy),
        "fault_trials_without_incident_group": sum(t["diagnostic_group_count"] == 0 for t in faults),
        "fault_trials_with_abstention": sum(t["abstained"] is True for t in faults),
        "qualified_fault_trials": len(qualified),
        "diagnostic_top1_hits": sum(t["diagnostic_top1_hit"] for t in faults),
        "diagnostic_top3_hits": sum(t["diagnostic_top3_hit"] for t in faults),
        "qualified_top1_hits": sum(t["qualified_top1_hit"] for t in faults)}
    return {**counts,
        "diagnostic_top1_per_fault_trial": counts["diagnostic_top1_hits"] / len(faults) if faults else None,
        "diagnostic_top3_per_fault_trial": counts["diagnostic_top3_hits"] / len(faults) if faults else None,
        "qualified_top1_per_fault_trial": counts["qualified_top1_hits"] / len(faults) if faults else None,
        "conditional_qualified_top1": counts["qualified_top1_hits"] / len(qualified) if qualified else None}


def evaluate_local_rca(store, dataset_ref, output, *, project=PROJECT):
    """Recompute G2/freeze before output or diagnostics; open only nonfinal development."""
    report = store.read(dataset_ref)
    _, cases = admitted_local_evaluation_rows(store, report)
    try:
        frozen_ref, frozen = read_binding(store, store.root / "dataset-freeze.json")
    except FileNotFoundError as exc:
        raise DatasetNotReady("RCA development requires a frozen source admission") from exc
    if frozen_ref != dataset_ref or frozen != report:
        raise DatasetNotReady("RCA dataset differs from its immutable admission freeze")
    commit = clean_source(project)
    protocol = load_protocol(project)
    if report["source_profile"] != protocol["source_profile"]:
        raise ValueError("RCA development source profile differs")
    rca_policy = json.loads((project / "configs/rca_policy.json").read_bytes())
    health_policy = json.loads((project / "configs/slo_health_policy.json").read_bytes())
    topology = yaml.safe_load((project / "configs/topology.yaml").read_text())
    for key, value in (("rca_policy", rca_policy), ("health_policy", health_policy), ("topology", topology)):
        if stable_id(value) != protocol[key + "_sha256"]:
            raise ValueError("RCA development policy/topology changed after protocol freeze")
    selected = [c for c in cases if c["role"] == "development-validation"]
    groups = defaultdict(list)
    for case in selected:
        groups[case["group_id"]].append(case["kind"])
    if (len(groups) < protocol["minimum_whole_groups"]
        or any(sorted(kinds) != sorted(KINDS) for kinds in groups.values())
        or len({c["run_id"] for c in selected}) != len(selected)):
        raise DatasetNotReady("RCA evaluation needs every development group's three original trials")
    output = Path(output).resolve()
    if output.is_relative_to(store.root):
        raise ValueError("RCA output must be outside the immutable source corpus")
    if output.exists():
        raise FileExistsError("RCA output must be new")
    inputs = []
    for case in sorted(selected, key=lambda c: (utc_time(c["started_at"]), c["run_id"])):
        result = store.read(case["source_result_ref"])
        relative = PurePosixPath(result["run_directory"])
        expected = ("runs", case["group_id"] + "-" + case["kind"], "captures", case["run_id"])
        if relative.parts != expected:
            raise ValueError("RCA source path differs from its admitted development trial")
        captured = _inside(store.root, str(relative))
        run_path = _inside(store.root, str(relative / "run.json"))
        _inside(store.root, str(relative / "serving"))
        run = json.loads(run_path.read_bytes())
        if (stable_id(run) != result["run_sha256"] or run["run_id"] != case["run_id"]
            or run["session_id"] != case["group_id"] or run["declared_split"] != "validation"
            or run["source_profile"] != protocol["source_profile"]):
            raise ValueError("RCA source run changed after admission")
        inputs.append((case, run, captured))
    output.mkdir(parents=True, exist_ok=False)
    trials = []
    for case, run, captured in inputs:
        source = ArtifactStore(captured / "serving")
        destination = output / case["run_id"]
        # Only operational refs and policies enter diagnostic functions. Neither
        # outcome labels nor intervention controls are diagnostic inputs.
        health = replay_health_manifest(source, run["source_manifest_ref"], health_policy,
            destination / "health", project=project)
        correlation = replay_correlation(ArtifactStore(destination / "health"), health["report_ref"],
            topology, destination / "correlation", project=project)
        manifest = source.read(run["source_manifest_ref"])
        cutoff = post_capture_cutoff(correlation["correlation"], source, manifest["evidence_refs"])
        diagnostic = replay_rca(source, ArtifactStore(destination / "health"),
            ArtifactStore(destination / "correlation"), correlation["report_ref"], rca_policy,
            destination / "rca", project=project, as_of=cutoff)
        trial = trial_result(case, run, diagnostic)
        trial.update(health_report_ref=health["report_ref"], correlation_report_ref=correlation["report_ref"],
                     analysis_cutoff=timestamp(cutoff))
        trials.append(trial)
    result = {"evaluation_version": "local-rca-development/v2", "dataset_ref": dataset_ref,
        "source_commit": commit, "protocol": protocol, "protocol_sha256": stable_id(protocol),
        "counts": summarize_trials(trials), "trials": trials,
        "comparison_method": "development-component-ablation/v1",
        "comparisons": summarize_comparisons(trials),
        "clock_mode": "post-capture-forensic-replay", "model_outputs_available": False,
        "source_corpus_required_for_reverification": True, "historical_emissions_verified": False,
        "full_G4_accepted": False, "holdout_opened": False, "activation_allowed": False,
        "actions_allowed": False,
        "limitations": ["Trial-level descriptive counts, not incident-level Q-04/Q-05 or final acceptance.",
            "Trials/windows within a whole context group are correlated; no IID confidence interval is claimed.",
            "The first onset/id group is evaluated, without selecting a favorable root or decision.",
            "All fault interventions remain in trial denominators, including no independent confirmed SLO.",
            "Forensic cutoff includes source receipts; no online detection, MTTD or causal timing claim.",
            "This replay excludes model outputs and cannot establish model-integrated RCA quality."]}
    (output / "evaluation.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result
