"""Pin validated inference to exact source windows; missing models stay missing."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.incidents.rca import COMPONENTS, evaluate_rca
from sentinelops.ml.registry import ModelRegistry, ModelUnavailable, IncompatibleFeatures
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp


class ModelIncidentEvidence:
    """Add a pinned model release to the canonical bounded evidence collector."""
    def __init__(self, collector, registry):
        self.collector, self.model_registry = collector, registry
        self.profile = {**collector.profile, "model_release_ref": registry.identity}

    async def verify_source(self, identity):
        return await self.collector.verify_source(identity)

    async def close(self):
        await self.collector.close()

    async def capture(self, store, *, source_state_ref, **kwargs):
        reference = await self.collector.capture(store, **kwargs)
        capture = store.read(reference)
        state = store.read(source_state_ref)
        batch = store.read(state["last_batch_ref"])
        normalization = store.read(batch["normalization_manifest_ref"])
        selected = {**normalization, "feature_refs": [ref for ref in normalization["feature_refs"]
            if capture["window_start"] <= store.read(ref)["window_end"] <= capture["window_end"]]}
        import asyncio
        signals = await asyncio.to_thread(collect_model_signals, store, selected,
            bucket_end=batch["bucket_end"], registry=self.model_registry)
        capture.update(capture_version="continuous-incident-evidence/v2", profile=self.profile,
            analyzed_at=timestamp(datetime.now(timezone.utc)), model_evidence={"release_ref": self.model_registry.identity,
                "source_state_ref": source_state_ref, "feature_batch_ref": state["last_batch_ref"], "signals": signals})
        return store.write("manifests", capture)


def verify_model_evidence(store, capture, registry):
    pack = capture["model_evidence"]
    if registry is None or registry.identity != pack["release_ref"] or capture["profile"]["model_release_ref"] != registry.identity:
        raise ValueError("model evidence requires the exact separately validated release")
    state = store.read(pack["source_state_ref"])
    if state["last_batch_ref"] != pack["feature_batch_ref"]:
        raise ValueError("model evidence changed its accepted feature batch")
    batch = store.read(pack["feature_batch_ref"])
    normalization = store.read(batch["normalization_manifest_ref"])
    expected_refs = {ref for ref in normalization["feature_refs"] if
        store.read(ref)["window_end"] == batch["bucket_end"] and
        capture["window_start"] <= store.read(ref)["window_end"] <= capture["window_end"]}
    if {s["feature_ref"] for _, s in pack["signals"]} != expected_refs or len(pack["signals"]) != len(expected_refs):
        raise ValueError("model evidence omits/duplicates or changes source feature windows")
    for reference, signal in pack["signals"]:
        envelope = store.read(signal["feature_ref"])
        if (store.read(reference) != signal or signal["service"] != envelope["service"]
            or signal["window_end"] != envelope["window_end"] or utc_time(signal["evaluated_at"]) > utc_time(capture["analyzed_at"])
            or signal["signal_id"] != stable_id({"feature_ref": signal["feature_ref"], "release_identity": registry.identity})):
            raise ValueError("model signal differs from its frozen feature/source clock")
        expected, reason = None, None
        decision = signal["inference"]["inferred_at"] if signal["inference"] is not None else signal["evaluated_at"]
        try:
            expected = registry.infer_envelope(envelope, now=utc_time(decision))
        except ModelUnavailable:
            reason = "no-validated-compatible-model"
        except IncompatibleFeatures:
            reason = "ineligible-incompatible-or-stale-feature"
        if expected != signal["inference"] or reason != signal["missing_reason"]:
            raise ValueError("model signal does not reproduce with the pinned actual estimator")
    return pack["signals"]


def project_model_signals(derived):
    group = next(g for g in derived["rca"]["correlation"]["groups"] if g["group_id"] == derived["group_id"])
    return [{"service": s["service"], "anomaly_score": s["inference"]["detection"]["anomaly_score"],
             "degradation_risk": s["inference"]["risk"]["risk_score"] if s["inference"]["risk"] is not None else None}
            for _, s in derived.get("model_signals", []) if s["status"] == "AVAILABLE" and s["service"] in group["affected_services"]]


def collect_model_signals(store, normalization, *, bucket_end, registry: ModelRegistry, now=None):
    signals, seen = [], set()
    for reference in normalization["feature_refs"]:
        envelope = store.read(reference)
        validate_payload("feature-envelope.schema.json", envelope)
        if envelope["window_end"] != bucket_end:
            continue
        service = envelope["service"]
        if service in seen:
            raise ValueError("duplicate current feature envelope for service")
        seen.add(service)
        output, reason, status = None, None, "AVAILABLE"
        try:
            output = registry.infer_envelope(envelope, now=now)
        except ModelUnavailable:
            reason, status = "no-validated-compatible-model", "UNAVAILABLE"
        except IncompatibleFeatures:
            reason, status = "ineligible-incompatible-or-stale-feature", "UNAVAILABLE"
        completed = now or datetime.now(timezone.utc)
        if output is not None:
            validate_payload("detection-result.schema.json", output["detection"])
            if output["risk"] is not None:
                validate_payload("degradation-risk.schema.json", output["risk"])
            for payload in (output["detection"], output["risk"]):
                if payload is not None and (payload["service"] != service or payload["observed_at"] != bucket_end
                    or payload["model_version"] != output["model_version"]):
                    raise ValueError("model result identity differs from exact feature window/version")
            if (output["feature_sha256"] != stable_id(envelope["payload"])
                or output["source_profile"] != envelope["source_profile"] or utc_time(output["inferred_at"]) > completed):
                raise ValueError("model inference source/clock differs from feature provenance")
        body = {"signal_version": "validated-window-model/v1", "service": service, "window_end": bucket_end,
                "feature_ref": reference, "status": status, "missing_reason": reason, "inference": output,
                "evaluated_at": timestamp(completed)}
        body["signal_id"] = stable_id({"feature_ref": reference, "release_identity": registry.identity})
        ref = store.write("signals", body)
        signals.append((ref, body))
    return signals


def add_model_support(store, row, signals, *, policy, as_of):
    """Add actual IF strength and risk facts, preserving every frozen RCA weight."""
    result = row["rca"]
    cards = [{"service": c["service"], "components": deepcopy(c["components"])} for c in result["candidates"]]
    evidence, availability = deepcopy(result["evidence"]), deepcopy(result["evidence_availability"])
    source_refs = set(result["source_refs"])
    services = {c["service"]: c for c in cards}
    seen = set()
    for reference, signal in signals:
        if store.read(reference) != signal:
            raise ValueError("model signal artifact identity differs")
        service = signal["service"]
        if service not in services or signal["status"] != "AVAILABLE":
            continue
        if service in seen:
            raise ValueError("duplicate model support for a service window")
        seen.add(service)
        inference = signal["inference"]
        feature = store.read(signal["feature_ref"])
        validate_payload("feature-envelope.schema.json", feature)
        if (utc_time(signal["evaluated_at"]) > as_of or utc_time(feature["window_end"]) > as_of
            or (as_of - utc_time(feature["window_end"])).total_seconds() > 180
            or feature["service"] != service or feature["window_end"] != signal["window_end"]
            or feature["payload"] is None or not feature["quality"]["eligible_for_inference"]
            or inference["feature_sha256"] != stable_id(feature["payload"])
            or inference["source_profile"] != feature["source_profile"]):
            raise ValueError("future/stale/incompatible model support")
        source_refs.update((reference, signal["feature_ref"]))
        detection = inference["detection"]
        validate_payload("detection-result.schema.json", detection)
        if (detection["service"] != service or detection["observed_at"] != signal["window_end"]
            or detection["model_version"] != inference["model_version"]):
            raise ValueError("model support belongs to a different service/window/version")
        fact = {"type": "model", "service": service, "observed_at": signal["window_end"], "source_ref": reference,
                "fact": f"Validated Isolation Forest inference: anomaly_score={detection['anomaly_score']}; model_version={inference['model_version']}; feature_ref={signal['feature_ref']}; risk_available={inference['risk'] is not None}"}
        fact["id"] = stable_id(fact); evidence.append(fact); availability[fact["id"]] = inference["inferred_at"]
        services[service]["components"]["anomaly_strength"] = {"value": detection["anomaly_score"], "method": "if-normalized-score/v1",
            "evidence_refs": [fact["id"]], "missing_reason": None}
        if inference["risk"] is not None:
            risk = inference["risk"]
            validate_payload("degradation-risk.schema.json", risk)
            fact = {"type": "model", "service": service, "observed_at": signal["window_end"], "source_ref": reference,
                    "fact": f"Validated XGBoost risk_score={risk['risk_score']}; state={risk['state']}; model_version={risk['model_version']}; risk is not a promised lead time or RCA causality"}
            fact["id"] = stable_id(fact); evidence.append(fact); availability[fact["id"]] = inference["inferred_at"]
    updated = evaluate_rca(result["correlation"], row["group_id"], evidence, cards, policy, as_of=as_of,
                           evidence_availability=availability, source_refs=sorted(source_refs))
    return {**row, "rca": updated, "rca_ref": store.write("manifests", updated)}
