"""Release builder boundaries; fixtures here never constitute an approval."""
import pytest

from sentinelops.ml.release import build_model_release, validate_parity_record, validate_v2_report

INDEX = {"candidate_id": "c" * 64, "models": [{"service": s, "model_version": s[0] * 64} for s in
         ("checkout-service", "inventory-service", "payment-service")]}
V1 = {"dataset_ref": "artifact://manifests/" + "d" * 64 + ".json", "training_manifest_sha256": "t" * 64,
      "source_profile": "prometheus-increase-60s-5s/v1",
      "models": [{"service": m["service"], "model_version": m["model_version"], "manifest_sha256": "m" * 64}
                 for m in INDEX["models"]]}


def parity(**change):
    return {"parity_version": "model-source-parity/v1", "candidate_id": "c" * 64, "environment": "lane-b-local",
            "source_profile": V1["source_profile"], "status": "PASS", "verified_by": "lane-b",
            "verified_at": "2026-11-08T10:00:00Z", "evidence": ["artifact://manifests/" + "e" * 64 + ".json"],
            "models": [{"service": m["service"], "model_version": m["model_version"]} for m in INDEX["models"]], **change}


def v2(**change):
    return {"evaluation_version": "local-model-development/v2", "authorizing_adr": "ADR-015", "G3_accepted": False,
            "activation_allowed": False, "holdout_opened": False, "dataset_ref": V1["dataset_ref"],
            "training_manifest_sha256": V1["training_manifest_sha256"], "source_profile": V1["source_profile"],
            "models": V1["models"], "services": {m["service"]: {} for m in INDEX["models"]}, **change}


def test_parity_record_must_certify_this_exact_candidate():
    validate_parity_record(parity(), INDEX, V1["source_profile"])
    for bad in (parity(status="FAIL"), parity(candidate_id="0" * 64), parity(models=parity()["models"][:2]),
                parity(source_profile="other/v1"), parity(evidence=[]), parity(verified_by=" "),
                {**parity(), "extra": 1}, parity(models=[{**m, "model_version": "x"} for m in parity()["models"]])):
        with pytest.raises(ValueError):
            validate_parity_record(bad, INDEX, V1["source_profile"])


def test_v2_report_must_pin_the_same_bundles_and_never_self_accept():
    validate_v2_report(v2(), V1)
    for bad in (v2(G3_accepted=True), v2(evaluation_version="local-model-development/v1"),
                v2(models=[{**m, "manifest_sha256": "x" * 64} for m in V1["models"]]),
                v2(dataset_ref="artifact://manifests/" + "0" * 64 + ".json"), v2(activation_allowed=True),
                v2(services={"payment-service": {}})):
        with pytest.raises(ValueError):
            validate_v2_report(bad, V1)


@pytest.mark.parametrize("field", ["approved_by", "decision_note"])
def test_release_requires_an_explicit_owner_decision_before_any_read(tmp_path, field):
    kwargs = {"approved_by": "owner", "decision_note": "accept G3", field: "  "}
    with pytest.raises(ValueError, match="owner decision"):
        build_model_release(tmp_path, "c" * 64, v2_report={}, parity_record={}, output=tmp_path / "out", **kwargs)
    assert not (tmp_path / "out").exists()


def test_release_output_is_exclusive(tmp_path):
    (tmp_path / "out").mkdir()
    with pytest.raises(FileExistsError):
        build_model_release(tmp_path, "c" * 64, v2_report={}, parity_record={}, approved_by="o",
                            decision_note="d", output=tmp_path / "out")
