"""Release builder boundaries; fixtures here never constitute an approval."""
import pytest

from sentinelops.ml.release import (
    PARITY_SCOPE, build_model_release, parity_verification_ref, validate_parity_record, validate_v2_report,
)
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore

INDEX = {"candidate_id": "c" * 64, "models": [{"service": s, "model_version": s[0] * 64} for s in
         ("checkout-service", "inventory-service", "payment-service")]}
V1 = {"dataset_ref": "artifact://manifests/" + "d" * 64 + ".json", "training_manifest_sha256": "t" * 64,
      "source_profile": "prometheus-increase-60s-5s/v1",
      "feature_set_version": "http-red-5m/v1", "training_data_policy_hash": "p" * 64,
      "protocol": {"protocol_version": "local-model-development/v1"},
      "models": [{"service": m["service"], "model_version": m["model_version"], "manifest_sha256": "m" * 64}
                 for m in INDEX["models"]]}
V1["protocol_sha256"] = stable_id(V1["protocol"])
CANDIDATE_CHECK = {"candidate_id": INDEX["candidate_id"], "development_predictions_reproduced": False}


def parity(**change):
    return {"parity_version": "model-source-parity/v1", "candidate_id": "c" * 64, "environment": "lane-b-local",
            "source_profile": V1["source_profile"], "status": "PASS", "verified_by": "lane-b",
            "verified_at": "2026-11-08T10:00:00Z", "evidence": ["artifact://manifests/" + "e" * 64 + ".json"],
            "models": [{"service": m["service"], "model_version": m["model_version"]} for m in INDEX["models"]], **change}


def verification():
    buckets = [{"service": model["service"], "bucket_end": f"2026-11-08T09:{minute}:00Z", "ok": True}
               for model in INDEX["models"] for minute in range(51, 56)]
    return {
        "version": "model-source-parity-check/v1", **{key: parity()[key] for key in PARITY_SCOPE},
        "replay": {**CANDIDATE_CHECK, "development_predictions_reproduced": True},
        "source_commit": "a" * 40, "source_shapes_sha256": "b" * 64,
        "normalization_ref": "artifact://manifests/" + "f" * 64 + ".json",
        "failures": [], "activation_allowed": False, "G3_accepted": False,
        "criteria": {"C2": [{"ok": True}, {"ok": True}, *buckets], "C3": [{"ok": True}, *buckets],
                     "C4": list(buckets), "C5": list(buckets),
                     "C6": [{**model, "ok": True} for model in INDEX["models"]]},
    }


def v2(**change):
    protocol = {"protocol_version": "local-model-development/v2", "base_protocol_sha256": V1["protocol_sha256"]}
    services = {m["service"]: {"abrupt_groups": 5, "abrupt_windows": 50, "abrupt_detector_events": {
        "isolation_forest": {"tp": 2}, "median_mad": {"tp": 1}}} for m in INDEX["models"]}
    return {"evaluation_version": "local-model-development/v2", "authorizing_adr": "ADR-015", "G3_accepted": False,
            "activation_allowed": False, "holdout_opened": False, "dataset_ref": V1["dataset_ref"],
            "training_manifest_sha256": V1["training_manifest_sha256"], "source_profile": V1["source_profile"],
            "feature_set_version": V1["feature_set_version"], "training_data_policy_hash": V1["training_data_policy_hash"],
            "models": V1["models"], "services": services, "protocol": protocol, "base_protocol": V1["protocol"],
            "protocol_sha256": stable_id(protocol), "detector_report_ref": "artifact://manifests/" + "a" * 64 + ".json",
            "predictions_ref": "artifact://manifests/" + "b" * 64 + ".json", **change}


def test_parity_record_must_certify_this_exact_candidate(tmp_path):
    report = verification()
    ref = ArtifactStore(tmp_path).write("manifests", report)
    assert ref == parity_verification_ref(report)
    validate_parity_record(parity(evidence=[ref]), INDEX, V1["source_profile"], report, CANDIDATE_CHECK)
    for bad in (parity(status="FAIL"), parity(candidate_id="0" * 64), parity(models=parity()["models"][:2]),
                parity(source_profile="other/v1"), parity(evidence=[]), parity(verified_by=" "),
                {**parity(), "extra": 1}, parity(models=[{**m, "model_version": "x"} for m in parity()["models"]])):
        with pytest.raises(ValueError):
            validate_parity_record(bad, INDEX, V1["source_profile"], report, CANDIDATE_CHECK)


def test_parity_rejects_relocated_path_and_changed_target():
    report = verification()
    original = parity(evidence=[parity_verification_ref(report)])
    for record, supplied in (
        (parity(evidence=["/b/verification.json"]), report),
        (original, {**report, "source_commit": "b" * 40}),
        ({**original, "environment": "another-target"}, report),
    ):
        with pytest.raises(ValueError, match="differs from the pinned decision"):
            validate_parity_record(record, INDEX, V1["source_profile"], supplied, CANDIDATE_CHECK)


def test_parity_requires_all_passes_and_unique_source_buckets():
    report = verification()
    without_replay = {**report, "replay": CANDIDATE_CHECK}
    with pytest.raises(ValueError, match="differs from the pinned decision"):
        validate_parity_record(parity(evidence=[parity_verification_ref(without_replay)]), INDEX,
                               V1["source_profile"], without_replay, CANDIDATE_CHECK)
    report["criteria"]["C5"][0] = {"ok": False}
    with pytest.raises(ValueError, match="complete passing"):
        validate_parity_record(parity(evidence=[parity_verification_ref(report)]), INDEX,
                               V1["source_profile"], report, CANDIDATE_CHECK)
    report["criteria"]["C5"][0] = report["criteria"]["C5"][1]
    with pytest.raises(ValueError, match="matched buckets"):
        validate_parity_record(parity(evidence=[parity_verification_ref(report)]), INDEX,
                               V1["source_profile"], report, CANDIDATE_CHECK)


def test_v2_report_must_pin_the_same_bundles_and_never_self_accept(tmp_path):
    store = ArtifactStore(tmp_path)
    reference = store.write("manifests", v2())
    validate_v2_report(store.read(reference), V1, reference)
    for bad in (v2(G3_accepted=True), v2(evaluation_version="local-model-development/v1"),
                v2(models=[{**m, "manifest_sha256": "x" * 64} for m in V1["models"]]),
                v2(dataset_ref="artifact://manifests/" + "0" * 64 + ".json"), v2(activation_allowed=True),
                v2(services={"payment-service": {}}), v2(predictions_ref="unbound.json"), v2(protocol_sha256="wrong"),
                v2(services={s: {} for s in v2()["services"]})):
        with pytest.raises(ValueError):
            validate_v2_report(bad, V1, store.write("manifests", bad))
    revised = v2()
    revised["services"]["payment-service"]["abrupt_detector_events"]["isolation_forest"]["tp"] = 3
    with pytest.raises(ValueError, match="reviewed reference"):
        validate_v2_report(revised, V1, reference)


@pytest.mark.parametrize("field", ["approved_by", "decision_note"])
def test_release_requires_an_explicit_owner_decision_before_any_read(tmp_path, field):
    kwargs = {"approved_by": "owner", "decision_note": "accept G3", field: "  "}
    with pytest.raises(ValueError, match="owner decision"):
        build_model_release(tmp_path, "c" * 64, v2_report={}, v2_report_ref="", parity_record={},
                            parity_verification={}, output=tmp_path / "out", **kwargs)
    assert not (tmp_path / "out").exists()


def test_release_output_is_exclusive(tmp_path):
    (tmp_path / "out").mkdir()
    with pytest.raises(FileExistsError):
        build_model_release(tmp_path, "c" * 64, v2_report={}, v2_report_ref="", parity_record={},
                            parity_verification={}, approved_by="o", decision_note="d", output=tmp_path / "out")
