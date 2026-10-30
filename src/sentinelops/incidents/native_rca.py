"""Research-only native evidence scoring; no correlation, SLO, IER or action writes."""
from __future__ import annotations

from sentinelops.incidents.rca import COMPONENTS, _score, _summarize, unavailable, validate_policy
from sentinelops.public_data.rcaeval_native import PROFILE_SHA256, digest

POLICY_SHA256 = "62ee6f9ed87a1dddcef3ef30cf45e662da887ce0f532a2638c6f4d9d5eab052e"
SCOPE_FIELDS = {"scope_version", "profile_sha256", "source_telemetry_sha256", "window_start_ns", "window_end_ns",
                "receipt_clock", "entities", "modalities", "log_support", "trace_support", "observed_parent_links",
                "unresolved_parent_links", "root_spans", "live_rca_compatible", "actions_allowed", "scope_sha256"}
NATIVE_METHODS = {name: set() for name in COMPONENTS}
NATIVE_METHODS["dependency_trace_relevance"] = {"native-observed-span-duration-fraction/v1"}
NATIVE_METHODS["log_support"] = {"native-exception-marker-presence/v1"}


def evaluate_native(scope: dict, policy: dict) -> dict:
    validate_policy(policy)
    if digest(policy) != POLICY_SHA256:
        raise ValueError("native evaluation requires the unchanged frozen RCA policy")
    if (not isinstance(scope, dict) or set(scope) != SCOPE_FIELDS
        or scope.get("scope_version") != "rcaeval-native-observations/v1"
        or scope.get("profile_sha256") != PROFILE_SHA256 or scope.get("receipt_clock") is not None
        or scope.get("live_rca_compatible") is not False or scope.get("actions_allowed") is not False
        or digest({k: v for k, v in scope.items() if k != "scope_sha256"}) != scope.get("scope_sha256")):
        raise ValueError("native scope identity/boundary differs")
    entities = scope.get("entities")
    if (not isinstance(entities, list) or not entities or any(not isinstance(e, str) or not e.strip() for e in entities)
        or entities != sorted(set(entities)) or len(entities) > 512):
        raise ValueError("native candidate identities differ")
    for modality, keys in (("trace_support", {"spans", "recorded_duration_ns"}),
                           ("log_support", {"records", "messages", "exception_markers"})):
        values = scope.get(modality)
        if not isinstance(values, dict) or not values.keys() <= set(entities):
            raise ValueError("native support belongs to an unobserved entity")
        for stats in values.values():
            if (not isinstance(stats, dict) or set(stats) != keys
                or any(type(n) is not int or n < 0 for n in stats.values())):
                raise ValueError("native support requires exact nonnegative counts")
            if modality == "log_support" and not stats["exception_markers"] <= stats["messages"] <= stats["records"]:
                raise ValueError("native log support counts are inconsistent")
            if modality == "trace_support" and not stats["spans"]:
                raise ValueError("native trace support requires observed spans")
    facts, candidates = {}, []
    total = sum(s["recorded_duration_ns"] for s in scope["trace_support"].values())
    for entity in scope["entities"]:
        cards = {name: unavailable(reason) for name, reason in zip(COMPONENTS, (
            "no-approved-source-compatible-model", "no-objective-SLO-onsets", "no-observed-native-spans",
            "no-observed-native-log-messages", "no-observed-deployment"))}
        trace = scope["trace_support"].get(entity)
        log = scope["log_support"].get(entity)
        if trace and not total:
            cards["dependency_trace_relevance"] = unavailable("zero-native-span-duration-denominator")
        for name, value, modality, method in (
            ("dependency_trace_relevance", trace["recorded_duration_ns"] / total if trace and total else None,
             "trace", "native-observed-span-duration-fraction/v1"),
            ("log_support", float(log["exception_markers"] > 0) if log and log["messages"] else None,
             "log", "native-exception-marker-presence/v1"),
        ):
            if value is not None:
                ref = digest([scope["scope_sha256"], entity, modality])
                facts[ref] = {"type": modality, "service": entity}
                cards[name] = {"value": value, "method": method, "evidence_refs": [ref], "missing_reason": None}
        score, coverage, refs = _score(cards, facts, entity, policy, methods=NATIVE_METHODS)
        candidates.append({"service": entity, "components": cards, "score": score,
                           "coverage": coverage, "evidence_refs": refs})
    ordered, ranking, margin, reasons = _summarize(candidates, policy, {"grouping_reason": "native-case-window"},
                                                   {ref: None for ref in facts})
    return {"result_version": "rcaeval-native-policy-result/v1", "scope_sha256": scope["scope_sha256"],
            "policy_sha256": POLICY_SHA256, "candidates": ordered, "diagnostic_ranking": ranking,
            "margin": margin, "abstained": bool(reasons), "abstention_reasons": reasons,
            "qualified_root": ranking[0] if ranking and not reasons else None,
            "source_availability": "unverified", "live_rca_compatible": False, "actions_allowed": False}
