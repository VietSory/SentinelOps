"""Scored suspected causes with explicit missing support and abstention."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import math

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.incidents.correlation import topology_paths
from sentinelops.telemetry.core import stable_id, timestamp

COMPONENTS = ("anomaly_strength", "temporal_precedence", "dependency_trace_relevance", "log_support", "recent_change_support")
METHODS = {
    "anomaly_strength": {"if-normalized-score/v1", "infra-connection-pressure/v1"},
    "temporal_precedence": {"disjoint-onset-intervals/v1"},
    "dependency_trace_relevance": {"native-span-duration-share/v1", "native-direct-span-duration-share/v1", "native-span-terminal-error/v1"},
    "log_support": {"business-completion-support/v1", "native-log-pattern-presence/v1"},
    "recent_change_support": {"observed-deployment/v1"},
}
FACT_TYPES = {"metric", "log", "trace", "topology", "change", "model", "timeline"}


def validate_policy(policy: dict) -> None:
    expected = {"policy_version", "weights", "minimum_score", "minimum_margin", "minimum_coverage",
                "onset_uncertainty_seconds", "evidence_window_seconds"}
    if not isinstance(policy, dict) or set(policy) != expected or policy["policy_version"] != "rca-evidence/local-v1":
        raise ValueError("unsupported RCA evidence policy")
    weights = policy["weights"]
    if not isinstance(weights, dict) or set(weights) != set(COMPONENTS):
        raise ValueError("RCA policy must declare every component weight")
    values = [*weights.values(), *(policy[k] for k in ("minimum_score", "minimum_margin", "minimum_coverage"))]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1 or not math.isfinite(v) for v in values):
        raise ValueError("RCA weights/thresholds must be finite unit-interval numbers")
    if not math.isclose(sum(weights.values()), 1., rel_tol=0, abs_tol=1e-12):
        raise ValueError("RCA weights must sum to one without missing-support redistribution")
    if type(policy["onset_uncertainty_seconds"]) is not int or not 0 <= policy["onset_uncertainty_seconds"] <= 3600:
        raise ValueError("invalid RCA onset uncertainty")
    if type(policy["evidence_window_seconds"]) is not int or not 60 <= policy["evidence_window_seconds"] <= 3600:
        raise ValueError("invalid RCA evidence window")


def unavailable(reason="not-observed") -> dict:
    return {"value": None, "method": "unavailable/v1", "evidence_refs": [], "missing_reason": reason}


def _score(components: dict, facts: dict, service: str, policy: dict, *, methods: dict | None = None) -> tuple[float | None, float, list[str]]:
    methods = METHODS if methods is None else methods
    if not isinstance(components, dict) or set(components) != set(COMPONENTS):
        raise ValueError("RCA candidate requires all component slots, including explicit missing values")
    score, coverage, support = 0., 0., set()
    for name in COMPONENTS:
        card = components[name]
        if not isinstance(card, dict) or set(card) != {"value", "method", "evidence_refs", "missing_reason"}:
            raise ValueError("invalid RCA component support card")
        value, refs = card["value"], card["evidence_refs"]
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs) or len(set(refs)) != len(refs):
            raise ValueError("RCA component evidence references must be unique strings")
        if value is None:
            if card["method"] != "unavailable/v1" or refs or not isinstance(card["missing_reason"], str) or not card["missing_reason"].strip():
                raise ValueError("missing RCA support requires a reason without fabricated evidence")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1 or not math.isfinite(value):
            raise ValueError("RCA component values must be finite numbers in [0,1]")
        if not isinstance(card["method"], str) or card["method"] not in methods[name] or card["missing_reason"] is not None or not refs or not set(refs) <= facts.keys():
            raise ValueError("RCA component method/evidence does not resolve")
        types = {facts[ref]["type"] for ref in refs}
        required = {"anomaly_strength": "model" if card["method"] == "if-normalized-score/v1" else "metric",
                    "temporal_precedence": "timeline", "dependency_trace_relevance": "trace",
                    "log_support": "log", "recent_change_support": "change"}[name]
        if required not in types:
            raise ValueError("RCA component lacks its required evidence modality")
        if not any(facts[ref]["type"] == required and facts[ref]["service"] == service for ref in refs):
            raise ValueError("RCA component evidence belongs to a different candidate")
        if name == "temporal_precedence" and len({facts[ref]["service"] for ref in refs if facts[ref]["type"] == "timeline"}) < 2:
            raise ValueError("RCA temporal support needs an explicit comparison")
        if card["method"] == "infra-connection-pressure/v1" and service != "rds":
            raise ValueError("infrastructure rule cannot impersonate a business-service IF score")
        if service == "rds" and card["method"] in ("if-normalized-score/v1", "business-completion-support/v1"):
            raise ValueError("RDS evidence cannot be fabricated as HTTP/IF output")
        score += policy["weights"][name] * float(value)
        coverage += policy["weights"][name]
        support.update(refs)
    return (score if coverage else None), coverage, sorted(support)


def temporal_precedence(correlation: dict, group: dict, service: str, policy: dict) -> float | None:
    """Compare disjoint onset intervals, never infer an order for tied buckets."""
    times = {f["service"]: utc_time(f["episode_start_at"]) for f in correlation["opening_facts"]
             if f["signal_id"] in group["signal_ids"]}
    if service not in times:
        return None
    width = timedelta(seconds=policy["onset_uncertainty_seconds"])
    relations = []
    for other, onset in times.items():
        if other == service:
            continue
        if times[service] + width < onset:
            relations.append(1.)
        elif onset + width < times[service]:
            relations.append(0.)
    return sum(relations) / len(relations) if relations else None


def _summarize(candidates: list[dict], policy: dict, group: dict, fact_availability: dict) -> tuple:
    ordered = sorted(candidates, key=lambda c: (-(c["score"] if c["score"] is not None else -1), c["service"]))
    known = [c for c in ordered if c["score"] is not None]
    positive = [c for c in known if c["score"] > 0]
    ranking, reasons, margin = [c["service"] for c in positive], [], None
    if not positive:
        reasons.append("no-positive-observed-support")
    else:
        top = positive[0]
        if len(known) >= 2:
            margin = top["score"] - known[1]["score"]
        if top["score"] < policy["minimum_score"]:
            reasons.append("insufficient-score")
        if margin is None or margin < policy["minimum_margin"]:
            reasons.append("insufficient-margin")
        if top["coverage"] < policy["minimum_coverage"]:
            reasons.append("insufficient-coverage")
    if any(c["score"] is None for c in ordered):
        reasons.append("unobserved-dependency-candidates")
    used = {ref for c in ordered for ref in c["evidence_refs"]}
    if any(fact_availability[ref] is None for ref in used):
        reasons.append("source-availability-unverified")
    if group["grouping_reason"] == "ambiguous-shared-consumer":
        reasons.append("ambiguous-correlation")
    return ordered, ranking, margin, sorted(set(reasons))


def _assemble_rca(correlation: dict, group_id: str, evidence: list[dict], support_cards: list[dict],
                 policy: dict, *, as_of: datetime, evidence_availability: dict,
                 source_refs: list[str]) -> dict:
    validate_policy(policy)
    validate_payload("correlation-result.schema.json", correlation)
    cutoff = timestamp(as_of)
    matching = [g for g in correlation["groups"] if g["group_id"] == group_id]
    if len(matching) != 1:
        raise ValueError("RCA group does not resolve in its correlation scope")
    group = matching[0]
    if as_of < max(utc_time(f["evaluated_at"]) for f in correlation["opening_facts"] if f["signal_id"] in group["signal_ids"]):
        raise ValueError("RCA decision precedes its opening evidence")
    paths = topology_paths(correlation["topology_snapshot"])
    universe = set().union(*(paths[service].keys() for service in group["affected_services"]))
    if (not isinstance(source_refs, list) or not source_refs
        or any(not isinstance(ref, str) or not ref.strip() for ref in source_refs) or len(set(source_refs)) != len(source_refs)):
        raise ValueError("RCA needs an explicit immutable source-reference allowlist")
    if not isinstance(evidence, list) or len(evidence) > 2000:
        raise ValueError("RCA evidence exceeds its bounded snapshot budget")
    facts = {}
    for fact in evidence:
        if not isinstance(fact, dict) or set(fact) != {"id", "type", "service", "observed_at", "fact", "source_ref"}:
            raise ValueError("RCA facts require complete source/time/content fields")
        if not isinstance(fact["id"], str) or not fact["id"] or fact["id"] in facts:
            raise ValueError("RCA fact identities must be nonempty and unique")
        if (not isinstance(fact["type"], str) or fact["type"] not in FACT_TYPES
            or (fact["service"] is not None and (not isinstance(fact["service"], str) or fact["service"] not in universe))
            or fact["source_ref"] not in source_refs or not isinstance(fact["fact"], str) or not 1 <= len(fact["fact"]) <= 2048):
            raise ValueError("RCA fact source/content is outside the candidate scope")
        if fact["observed_at"] is None and fact["type"] != "topology":
            raise ValueError("RCA event evidence requires an observed timestamp")
        if fact["observed_at"] is not None and (not isinstance(fact["observed_at"], str) or utc_time(fact["observed_at"]) > as_of):
            raise ValueError("RCA evidence observes a future fact")
        facts[fact["id"]] = fact
    if not isinstance(evidence_availability, dict) or set(evidence_availability) != set(facts):
        raise ValueError("RCA must account for every fact's source availability")
    for key, value in evidence_availability.items():
        if value is not None:
            if (not isinstance(value, str) or utc_time(value) > as_of
                or (facts[key]["observed_at"] is not None and utc_time(value) < utc_time(facts[key]["observed_at"]))):
                raise ValueError("RCA evidence was unavailable or clock-inconsistent at its decision cutoff")
    cards = {}
    for item in support_cards:
        if not isinstance(item, dict) or set(item) != {"service", "components"} or item["service"] not in universe or item["service"] in cards:
            raise ValueError("RCA candidate support duplicates or escapes its topology scope")
        cards[item["service"]] = item["components"]
    candidates = []
    for service in sorted(universe):
        components = deepcopy(cards.get(service, {key: unavailable() for key in COMPONENTS}))
        score, coverage, refs = _score(components, facts, service, policy)
        temporal = components["temporal_precedence"]["value"]
        expected_temporal = temporal_precedence(correlation, group, service, policy)
        if temporal is not None and (expected_temporal is None or not math.isclose(temporal, expected_temporal, rel_tol=0, abs_tol=1e-12)):
            raise ValueError("RCA temporal precedence contradicts observed onset uncertainty")
        for card in components.values():
            card["evidence_refs"] = sorted(card["evidence_refs"])
            if card["value"] is not None:
                card["value"] = float(card["value"])
        candidates.append({"service": service, "components": components, "score": score,
                           "coverage": coverage, "evidence_refs": refs})
    ordered, ranking, margin, reasons = _summarize(candidates, policy, group, evidence_availability)
    result = {"schema_version": "1.0", "rca_version": "evidence-rca/v1", "mode": "event-time-analytical",
        "as_of": cutoff, "policy": deepcopy(policy), "policy_sha256": stable_id(policy),
        "correlation": deepcopy(correlation), "group_id": group_id, "source_refs": sorted(source_refs),
        "evidence": sorted(deepcopy(evidence), key=lambda f: f["id"]), "evidence_availability": deepcopy(evidence_availability),
        "candidates": ordered, "ranking": ranking, "margin": margin, "abstained": bool(reasons),
        "abstention_reasons": reasons, "actions_allowed": False, "historical_emissions_verified": False}
    result["result_id"] = stable_id(result)
    return result


def evaluate_rca(correlation: dict, group_id: str, evidence: list[dict], support_cards: list[dict],
                 policy: dict, *, as_of: datetime, evidence_availability: dict, source_refs: list[str]) -> dict:
    result = _assemble_rca(correlation, group_id, evidence, support_cards, policy, as_of=as_of,
                           evidence_availability=evidence_availability, source_refs=source_refs)
    validate_payload("rca-result.schema.json", result)
    return result


def validate_result(result: dict) -> None:
    cards = [{"service": c["service"], "components": c["components"]} for c in result["candidates"]]
    expected = _assemble_rca(result["correlation"], result["group_id"], result["evidence"], cards,
        result["policy"], as_of=utc_time(result["as_of"]), evidence_availability=result["evidence_availability"],
        source_refs=result["source_refs"])
    if result != expected:
        raise ValueError("RCA scores/coverage/ranking/abstention differ from their scoped evidence and policy")


def rank_native_support(services: list[str], evidence: list[dict], cards: list[dict], policy: dict):
    """Source-neutral scoring kernel for supplementary native development reports.

No synthetic SLO health/correlation is constructed for foreign native services.
The local RCA wire contract remains unchanged. Native receipt clocks are unknown.
"""
    validate_policy(policy)
    facts = {fact["id"]: fact for fact in evidence}
    if len(facts) != len(evidence) or len(set(services)) != len(services) or not services:
        raise ValueError("native candidate/fact identities must be unique")
    support = {c["service"]: c["components"] for c in cards}
    if len(support) != len(cards) or set(support) != set(services):
        raise ValueError("native support must match observed native service scope")
    candidates = []
    for service in sorted(services):
        score, coverage, refs = _score(support[service], facts, service, policy)
        candidates.append({"service": service, "score": score, "coverage": coverage,
                           "evidence_refs": refs, "components": deepcopy(support[service])})
    ordered, ranking, margin, reasons = _summarize(candidates, policy, {"grouping_reason": "native-observation-scope"},
                                                  {key: None for key in facts})
    return {"native_result_version": "native-rca-development/v1", "policy_sha256": stable_id(policy),
            "candidates": ordered, "ranking": ranking, "margin": margin, "abstained": bool(reasons),
            "abstention_reasons": reasons, "evidence": deepcopy(evidence), "actions_allowed": False,
            "historical_emissions_verified": False}
