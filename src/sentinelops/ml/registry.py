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
from sentinelops.telemetry.core import BUSINESS_ROUTES, FEATURE_SET, stable_id
from sentinelops.telemetry.store import ArtifactStore


class ModelUnavailable(RuntimeError):
    pass


class IncompatibleFeatures(ValueError):
    pass


class ModelRegistry:
    def __init__(self):
        self._models = {}
        self.identity = "unavailable/v1"

    @classmethod
    def load(cls, store: ArtifactStore, release_ref: str):
        release = store.read(release_ref)
        if release.get("release_version") != "validated-model-release/v1":
            raise ValueError("unsupported model release ledger")
        registry = cls()
        from sentinelops.ml.models import load_bundle
        for item in release["models"]:
            directory = (store.root / item["bundle_directory"]).resolve()
            if not directory.is_relative_to(store.root):
                raise ValueError("model path escapes trusted store")
            manifest, detector, risk = load_bundle(directory, expected_manifest_sha256=item["manifest_sha256"])
            approval = store.read(item["validation_ref"])
            if (manifest["origin"] != "admitted-gamma" or approval.get("model_version") != manifest["model_version"]
                or approval.get("G2") != "PASS" or approval.get("G3") != "PASS" or approval.get("source_parity") != "PASS"
                or approval.get("activation_allowed") is not True or not approval.get("evaluation_refs")):
                raise ModelUnavailable("model lacks separately accepted G2/G3 and live source compatibility")
            for ref in approval["evaluation_refs"]:
                store.read(ref)
            provenance = manifest["provenance"]
            service = provenance["service"]
            if service in registry._models or provenance["feature_set_version"] != FEATURE_SET:
                raise IncompatibleFeatures("duplicate model route or incompatible live feature set")
            registry._models[service] = (manifest, detector, risk, item["validation_ref"])
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
        manifest, detector, risk_model, validation_ref = route
        if (source_profile != manifest["provenance"]["source_profile"]
            or set(feature["features"]) != set(manifest["ordered_features"])):
            raise IncompatibleFeatures("feature/source schema differs from the pinned bundle")
        current = now or datetime.now(timezone.utc)
        end = utc_time(feature["window_end"])
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
        if risk_model is not None:
            value = float(risk_model.predict_proba([values])[0, 1])
            watch, degrading = manifest["risk_thresholds"]["watch"], manifest["risk_thresholds"]["degrading"]
            risk = {"schema_version": "1.0", "service": feature["service"], "observed_at": feature["window_end"],
                    "risk_score": value, "state": "degrading" if value >= degrading else "watch" if value >= watch else "normal",
                    "model_name": "xgboost", "model_version": manifest["model_version"]}
            validate_payload("degradation-risk.schema.json", risk)
        completed = now or datetime.now(timezone.utc)
        return {"detection": detection, "risk": risk, "model_version": manifest["model_version"], "validation_ref": validation_ref,
                "feature_sha256": stable_id(feature), "source_profile": source_profile, "inferred_at": completed.isoformat().replace("+00:00", "Z")}

    def infer_envelope(self, envelope, *, now=None):
        validate_payload("feature-envelope.schema.json", envelope)
        if not envelope["quality"]["eligible_for_inference"] or envelope["payload"] is None:
            raise IncompatibleFeatures("feature quality does not permit inference")
        current = now or datetime.now(timezone.utc)
        if utc_time(envelope["as_of"]) > current:
            raise IncompatibleFeatures("feature was not available at inference cutoff")
        return self.infer(envelope["payload"], source_profile=envelope["source_profile"], now=now)
