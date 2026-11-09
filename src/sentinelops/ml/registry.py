"""Trusted local bundle registry; serving is unavailable until separate validation.

An operator-controlled release ledger pins bundle checksums and independent
validation/parity evidence. Offline training cannot write that approval. API
requests cannot upload artifacts, change routing, or grant activation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.ml.alibaba_bundle import BUNDLE_VERSION as ALIBABA_BUNDLE_VERSION
from sentinelops.telemetry.core import BUSINESS_ROUTES, FEATURE_KEYS, FEATURE_SET, stable_id
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2
from sentinelops.telemetry.store import ArtifactStore


class ModelUnavailable(RuntimeError):
    pass


class IncompatibleFeatures(ValueError):
    pass


def complete_inference(output, completed_at):
    """Preserve the actual receipt; expire v2 risk when its anchor advances.

    Numerical replay uses the recorded start. Qualification uses completion too,
    so work started before an onset minute cannot masquerade as an early warning
    when its result was available only after that minute began.
    """
    result = {**output, "inferred_at": completed_at}
    availability = output.get("risk_availability")
    if availability is not None and utc_time(completed_at) >= utc_time(availability["anchor_at"]) + timedelta(seconds=60):
        result["risk"] = None
        result["risk_availability"] = {**availability, "available":False,
                                       "missing_reason":"forecast-anchor-minute-advanced"}
    return result


class ModelRegistry:
    def __init__(self):
        self._models = {}
        self.identity = "unavailable/v1"
        self.is_alibaba = False

    @classmethod
    def load(cls, store: ArtifactStore, release_ref: str):
        release = store.read(release_ref)
        if release.get("release_version") != "validated-model-release/v1":
            raise ValueError("unsupported model release ledger")
        from sentinelops.ml.models import load_bundle, read_bundle_manifest

        # Check every route before loading any estimator. One invalid approval
        # rejects the release without deserializing even an earlier valid route.
        approved = {}
        for item in release["models"]:
            directory = (store.root / item["bundle_directory"]).resolve()
            if not directory.is_relative_to(store.root):
                raise ValueError("model path escapes trusted store")
            manifest = read_bundle_manifest(directory, expected_manifest_sha256=item["manifest_sha256"])
            approval = store.read(item["validation_ref"])
            if manifest["bundle_version"] == ALIBABA_BUNDLE_VERSION:
                if len(release["models"]) != 1:
                    raise IncompatibleFeatures("Alibaba release requires one shared bundle for all business routes")
                from sentinelops.ml.alibaba_training import load_config
                provenance = manifest["provenance"]
                if (approval.get("model_version") != manifest["model_version"]
                    or approval.get("G2") != "PASS" or approval.get("G3") != "PASS"
                    or approval.get("source_parity") != "PASS" or approval.get("activation_allowed") is not True
                    or not approval.get("evaluation_refs") or approval.get("source_profile") != SOURCE_PROFILE_V2
                    or any(approval.get(key) != provenance[key] for key in ("report_sha256", "config_sha256"))):
                    raise ModelUnavailable("Alibaba model lacks separately accepted G2/G3 and v2 source parity")
                if (provenance["config"] != load_config()
                    or hashlib.sha256((directory / "report.json").read_bytes()).hexdigest() != provenance["report_sha256"]):
                    raise IncompatibleFeatures("Alibaba source report or frozen feature config differs")
                for ref in approval["evaluation_refs"]:
                    store.read(ref)
                manifest, detector, risk = load_bundle(directory, expected_manifest_sha256=item["manifest_sha256"])
                registry = cls()
                registry._models = {service: (manifest, detector, risk, item["validation_ref"])
                                    for service in BUSINESS_ROUTES}
                registry.identity = release_ref
                registry.is_alibaba = True
                return registry
            if (manifest["origin"] != "admitted-local-http" or manifest.get("bundle_version") != "sentinelops-model/v2"
                or approval.get("model_version") != manifest["model_version"]
                or approval.get("G2") != "PASS" or approval.get("G3") != "PASS" or approval.get("source_parity") != "PASS"
                or approval.get("activation_allowed") is not True or not approval.get("evaluation_refs")
                or approval.get("dataset_ref") != manifest["provenance"].get("dataset_ref")
                or approval.get("training_data_policy_hash") != manifest["provenance"].get("training_data_policy_hash")):
                raise ModelUnavailable("model lacks separately accepted G2/G3 and live source compatibility")
            for ref in approval["evaluation_refs"]:
                store.read(ref)
            provenance = manifest["provenance"]
            validate_payload("training-data-policy-v2.schema.json", provenance.get("training_policy", {}))
            from sentinelops.public_data.policy import policy_hash
            if provenance.get("training_data_policy_hash") != policy_hash(provenance["training_policy"]):
                raise IncompatibleFeatures("model training source differs from its approved contract")
            service = provenance["service"]
            if (service not in BUSINESS_ROUTES or service in approved
                or provenance["feature_set_version"] != FEATURE_SET
                or set(manifest["ordered_features"]) != set(FEATURE_KEYS)
                or provenance.get("source_profile") != provenance["training_policy"]["sources"]["local_http"]["source_profile"]):
                raise IncompatibleFeatures("duplicate model route or incompatible live feature set")
            approved[service] = (directory, item["manifest_sha256"], item["validation_ref"])

        registry = cls()
        for service, (directory, checksum, validation_ref) in approved.items():
            manifest, detector, risk = load_bundle(directory, expected_manifest_sha256=checksum)
            registry._models[service] = (manifest, detector, risk, validation_ref)
        registry.identity = release_ref
        return registry

    @property
    def available(self):
        return all(service in self._models for service in BUSINESS_ROUTES)

    def infer(self, feature: dict, *, source_profile: str | None, now=None):
        validate_payload("feature-window.schema.json", feature)
        route = self._models.get(feature["service"])
        if route is None:
            raise ModelUnavailable("no validated compatible model for this service")
        manifest = route[0]
        if self.is_alibaba:
            raise IncompatibleFeatures("Alibaba inference requires accepted v2 observation history")
        if (source_profile != manifest["provenance"]["source_profile"]
            or set(feature["features"]) != set(manifest["ordered_features"])):
            raise IncompatibleFeatures("feature/source schema differs from the pinned bundle")
        return self._predict(route, feature, source_profile, now)

    def infer_accepted_history(self, store, batch_ref, service, *, now=None):
        """Return the exact relative projection and inference for a pinned health batch."""
        route = self._models.get(service)
        if route is None or not self.is_alibaba:
            raise ModelUnavailable("no validated Alibaba model for this service")
        from sentinelops.ml.alibaba_live import project_relative_window
        current = now or datetime.now(timezone.utc)
        feature = project_relative_window(store, batch_ref, service, as_of=current,
                                          config=route[0]["provenance"]["config"])
        if feature is None:
            raise IncompatibleFeatures("insufficient accepted v2 observations for relative inference")
        return feature, self._predict(route, feature, SOURCE_PROFILE_V2, current)

    def _predict(self, route, feature, source_profile, now):
        manifest, detector, risk_model, validation_ref = route
        current = now or datetime.now(timezone.utc)
        end = utc_time(feature["window_end"])
        if end - utc_time(feature["window_start"]) != timedelta(seconds=300):
            raise IncompatibleFeatures("live feature profile requires a 300-second historical window")
        minute_causal = self.is_alibaba or manifest.get("origin") == "admitted-local-http"
        if minute_causal and (end.second or end.microsecond):
            raise IncompatibleFeatures("validated local source requires UTC minute-aligned windows")
        if end > current or current - end > timedelta(seconds=180):
            raise IncompatibleFeatures("future or stale feature window")
        values = [feature["features"][key] for key in manifest["ordered_features"]]
        if any(type(value) not in (float, int) or not math.isfinite(value) for value in values):
            raise IncompatibleFeatures("missing/nonfinite required model feature")
        actual = detector.predict([values])["isolation_forest"]
        detection = {"schema_version": "1.0", "service": feature["service"], "observed_at": feature["window_end"],
                     "anomaly_score": float(actual["score"][0]), "is_anomalous": bool(actual["alert"][0]),
                     "model_name": "isolation-forest", "model_version": manifest["model_version"]}
        validate_payload("detection-result.schema.json", detection)
        risk = None
        risk_clock_valid = not minute_causal or current < end + timedelta(seconds=60)
        if risk_model is not None and risk_clock_valid:
            value = float(risk_model.predict_proba([values])[0, 1])
            watch, degrading = manifest["risk_thresholds"]["watch"], manifest["risk_thresholds"]["degrading"]
            risk = {"schema_version": "1.0", "service": feature["service"], "observed_at": feature["window_end"],
                    "risk_score": value, "state": "degrading" if value >= degrading else "watch" if value >= watch else "normal",
                    "model_name": "xgboost", "model_version": manifest["model_version"]}
            validate_payload("degradation-risk.schema.json", risk)
        completed = now or datetime.now(timezone.utc)
        output = {"detection": detection, "risk": risk, "model_version": manifest["model_version"], "validation_ref": validation_ref,
                "feature_sha256": stable_id(feature), "source_profile": source_profile, "inferred_at": completed.isoformat().replace("+00:00", "Z")}
        if minute_causal:
            output["risk_availability"] = {"anchor_at":feature["window_end"],"horizon_seconds":300,
                "available":risk is not None, "missing_reason":None if risk is not None else
                "forecast-anchor-minute-advanced" if not risk_clock_valid else "no-risk-estimator"}
        return complete_inference(output, output["inferred_at"])

    def infer_envelope(self, envelope, *, now=None):
        validate_payload("feature-envelope.schema.json", envelope)
        if not envelope["quality"]["eligible_for_inference"] or envelope["payload"] is None:
            raise IncompatibleFeatures("feature quality does not permit inference")
        current = now or datetime.now(timezone.utc)
        if utc_time(envelope["as_of"]) > current:
            raise IncompatibleFeatures("feature was not available at inference cutoff")
        return self.infer(envelope["payload"], source_profile=envelope["source_profile"], now=now)
