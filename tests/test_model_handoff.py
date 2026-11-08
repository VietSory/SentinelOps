"""Handoff software tests never supply a real admission/model quality approval."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from sentinelops.ml.handoff import _copy_asset, _development_table, export_model_candidate
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.ml.registry import ModelRegistry
from sentinelops.telemetry.store import ArtifactStore


def prediction():
    original = {"feature_ref": "artifact://features/" + "a"*64 + ".json",
                "service": "payment-service", "split": "development-validation",
                "group_id": "dev-01", "features": [1.0]*5, "risk_label": 1,
                "prediction_cutoff": "2026-11-04T00:05:30Z"}
    selected = {**original, "decision_at": original["prediction_cutoff"],
        "isolation_forest_score": .8, "isolation_forest_flag": False,
        "median_mad_score": .7, "median_mad_flag": False,
        "xgboost_score": .8, "xgboost_flag": True, "slope_score": 1., "slope_flag": True}
    return original, selected


def test_copy_preserves_bytes_and_never_overwrites(tmp_path):
    source, target = tmp_path/"source", tmp_path/"runtime"
    (source/"service").mkdir(parents=True)
    data=b"opaque trusted asset fixture; not a model"
    (source/"service/asset").write_bytes(data)
    digest=hashlib.sha256(data).hexdigest()
    assert _copy_asset(source,"service/asset",target,digest)==digest
    assert (target/"service/asset").read_bytes()==data
    with pytest.raises(FileExistsError):
        _copy_asset(source,"service/asset",target,digest)


@pytest.mark.parametrize("relative", ["../outside", "/absolute", "link/asset"])
def test_copy_rejects_source_escape_and_links(tmp_path, relative):
    source=tmp_path/"source";source.mkdir()
    outside=tmp_path/"outside";outside.mkdir()
    (source/"link").symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError):
        _copy_asset(source,relative,tmp_path/"target","0"*64)
    assert not (tmp_path/"target").exists()


def test_copy_checksum_mismatch_creates_no_asset(tmp_path):
    source=tmp_path/"source";source.mkdir();(source/"asset").write_bytes(b"changed")
    with pytest.raises(ValueError,match="checksum"):
        _copy_asset(source,"asset",tmp_path/"target","0"*64)
    assert not (tmp_path/"target").exists()


def test_only_exact_original_development_rows_can_be_materialized():
    original, selected = prediction()
    _development_table({"table_version":"local-development-predictions/v1","rows":[selected]},[original])
    for change in ({"split":"sealed-final"},{"risk_label":True},{"features":[False]*5},
                   {"service":"inventory-service"},{"unexpected_secret":"not-a-real-secret"},
                   {"xgboost_score":float("nan")},{"decision_at":"2026-11-04T00:06:30Z"}):
        with pytest.raises(ValueError):
            _development_table({"table_version":"local-development-predictions/v1","rows":[{**selected,**change}]},[original])
    for rows in ([],[selected,selected]):
        with pytest.raises(ValueError):
            _development_table({"table_version":"local-development-predictions/v1","rows":rows},[original])


@pytest.mark.parametrize("forged_ready",[False,True])
def test_partial_or_forged_admission_never_opens_model_or_evaluation_bytes(tmp_path,monkeypatch,forged_ready):
    source=ArtifactStore(tmp_path/"source")
    ref=source.write("manifests",{"dataset_version":"local-http-admission/v1","training_ready":forged_ready,
                                  "created_at":"2026-11-01T00:00:00Z"})
    def forbidden(*args,**kwargs):
        pytest.fail("candidate bytes accessed before original source admission")
    monkeypatch.setattr("sentinelops.ml.handoff._read_pinned",forbidden)
    output=tmp_path/"export"
    with pytest.raises(DatasetNotReady):
        export_model_candidate(source,ref,tmp_path/"missing-model",ArtifactStore(tmp_path/"missing-eval"),
            "artifact://manifests/"+"0"*64+".json",training_manifest_sha256="0"*64,output=output)
    assert not output.exists()


def test_candidate_descriptor_never_counts_as_serving_release(tmp_path):
    store=ArtifactStore(tmp_path)
    ref=store.write("manifests",{"handoff_version":"unapproved-model-candidate/v1","models":[],
                                 "activation_allowed":False,"holdout_opened":False})
    with pytest.raises(ValueError,match="unsupported model release"):
        ModelRegistry.load(store,ref)


def test_g3_packet_rejects_untrusted_candidate_before_loading_models(tmp_path, monkeypatch):
    from scripts import g3_review_packet
    from sentinelops.telemetry.core import stable_id

    index = {"handoff_version": "unapproved-model-candidate/v1", "activation_allowed": False,
             "holdout_opened": False, "original_corpus_included": False, "source_parity": "not-accepted",
             "runtime_root": "runtime", "audit_root": "audit"}
    (tmp_path / "handoff.json").write_text(json.dumps({**index, "candidate_id": stable_id(index)}))

    def forbidden(*args, **kwargs):
        pytest.fail("review accessed audit or model files for the wrong candidate")

    monkeypatch.setattr(g3_review_packet, "_audit", forbidden)
    monkeypatch.setattr(g3_review_packet, "scaler_diagnostic", forbidden)
    with pytest.raises(ValueError, match="candidate identity"):
        g3_review_packet.render(tmp_path, "0" * 64, {}, "unused")


def test_export_cli_reports_real_source_gate_before_missing_assets(tmp_path):
    source=ArtifactStore(tmp_path/"source")
    ref=source.write("manifests",{"dataset_version":"local-http-admission/v1","training_ready":False})
    completed=subprocess.run([sys.executable,"-m","scripts.export_model_candidate",
        "--store",str(source.root),"--dataset-ref",ref,"--models",str(tmp_path/"missing"),
        "--evaluation-store",str(tmp_path/"eval"),"--evaluation-ref","artifact://manifests/"+"0"*64+".json",
        "--training-manifest-sha256","0"*64,"--output",str(tmp_path/"output")],
        cwd=Path(__file__).parents[1],capture_output=True,text=True,timeout=30)
    assert completed.returncode==2
    result=json.loads(completed.stdout)
    assert result["status"]=="BLOCKED" and not result["activation_allowed"] and not result["holdout_opened"]
    assert not (tmp_path/"output").exists()


def test_portable_layout_separates_runtime_and_audit_and_preserves_original_bytes(tmp_path,monkeypatch):
    """Mock the trusted source/loader boundaries to test materialization, not admission."""
    from copy import deepcopy
    import shutil
    from types import SimpleNamespace
    import sentinelops.ml.handoff as handoff
    from sentinelops.experiments.local_corpus import bind
    from sentinelops.telemetry.core import BUSINESS_ROUTES,FEATURE_KEYS,FEATURE_SET,stable_id

    services=sorted(BUSINESS_ROUTES)
    source=ArtifactStore(tmp_path/"corpus")
    report={"dataset_version":"local-http-admission/v1","training_ready":True,"services":services,
            "source_profile":"prometheus-increase-60s-5s/v1","feature_set_version":FEATURE_SET,
            "training_data_policy_hash":"pinned-policy-fixture","counts":{r:{s:{"healthy_windows":1} for s in services}
            for r in ("train","calibration","development-validation")}}
    dataset_ref=bind(source,source.root/"dataset-freeze.json",report)
    rows=[]
    for service in services:
        for split in ("train","calibration","development-validation"):
            rows.append({"service":service,"split":split,"feature_ref":service+"-"+split,
                "features":[1.0]*5,"healthy":True,"risk_label":0,
                "prediction_cutoff":"2026-11-04T00:05:30Z"})
    monkeypatch.setattr(handoff,"admitted_local_evaluation_rows",lambda *_:(deepcopy(rows),[]))
    monkeypatch.setattr(handoff,"clean_source",lambda *_:"a"*40)
    model_root=tmp_path/"candidate";model_root.mkdir()
    fitted=[];pins=[];headers={}
    for service in services:
        relative=stable_id(service);directory=model_root/relative;directory.mkdir()
        artifacts={"detector.joblib":b"opaque unit-test weights","risk.json":b"{}"}
        for name,data in artifacts.items():(directory/name).write_bytes(data)
        train=[r for r in rows if r["service"]==service and r["split"]=="train"]
        cal=[r for r in rows if r["service"]==service and r["split"]=="calibration"]
        header={"bundle_version":"sentinelops-model/v2","origin":"admitted-local-http","activation_allowed":False,
            "model_version":"fixture-"+service,"ordered_features":list(FEATURE_KEYS),"library_versions":{},
            "thresholds":{},"risk_thresholds":{"watch":.5,"degrading":.8},"detector_config":{"n_estimators":100},
            "artifacts":{name:hashlib.sha256(data).hexdigest() for name,data in artifacts.items()},
            "provenance":{"dataset_ref":dataset_ref,"dataset_report":report,"service":service,
                "source_profile":report["source_profile"],"feature_set_version":FEATURE_SET,
                "training_data_policy_hash":report["training_data_policy_hash"],"risk_horizon_seconds":300,
                "training_rows_sha256":stable_id(train),"calibration_rows_sha256":stable_id(cal),
                "risk_training_rows_sha256":stable_id(train),"fitting_source_commit":"b"*40}}
        data=json.dumps(header).encode();(directory/"manifest.json").write_bytes(data)
        headers[relative]=header
        fitted.append({"service":service,"model_version":header["model_version"],
                       "bundle_directory":"/sender-only/"+relative,"activation_allowed":False})
        pins.append({"service":service,"model_version":header["model_version"],
                     "manifest_sha256":hashlib.sha256(data).hexdigest()})
    training={"training_manifest_version":"local-http-fit/v1","dataset_ref":dataset_ref,
              "models":fitted,"activation_allowed":False,"holdout_opened":False}
    training_bytes=json.dumps(training).encode();(model_root/"training-manifest.json").write_bytes(training_bytes)
    training_sha=hashlib.sha256(training_bytes).hexdigest()
    evaluation=ArtifactStore(tmp_path/"evaluation")
    predicted=[]
    for original in (r for r in rows if r["split"]=="development-validation"):
        _,example=prediction()
        predicted.append({**original,**{k:v for k,v in example.items() if k.endswith(("_score","_flag"))},
                          "decision_at":original["prediction_cutoff"]})
    predicted_ref=evaluation.write("manifests",{"table_version":"local-development-predictions/v1","rows":predicted})
    evaluation_ref=evaluation.write("manifests",{"evaluation_version":"local-model-development/v1",
        "dataset_ref":dataset_ref,"training_manifest_sha256":training_sha,"source_profile":report["source_profile"],
        "feature_set_version":FEATURE_SET,"training_data_policy_hash":report["training_data_policy_hash"],
        "models":pins,"predictions_ref":predicted_ref,"activation_allowed":False,"holdout_opened":False,
        "G3_accepted":False,"services":{s:{"fixture_only":True} for s in services},"limitations":["unit fixture only"]})
    def verified_loader(directory,**kwargs):return headers[directory.name],object(),object()
    monkeypatch.setitem(sys.modules,"sentinelops.ml.models",SimpleNamespace(load_bundle=verified_loader))
    output=tmp_path/"handoff"
    index=export_model_candidate(source,dataset_ref,model_root,evaluation,evaluation_ref,
                                training_manifest_sha256=training_sha,output=output)
    recipient=tmp_path/"recipient";shutil.copytree(output,recipient)
    assert not index["activation_allowed"] and not index["holdout_opened"]
    assert index["handoff_version"]=="unapproved-model-candidate/v1" and len(index["models"])==3
    assert not (recipient/"runtime/manifests").exists()
    assert (recipient/"audit/training-manifest.json").read_bytes()==training_bytes
    for relative,digest in index["asset_sha256"].items():
        assert hashlib.sha256((recipient/relative).read_bytes()).hexdigest()==digest
    for selected in index["models"]:
        assert not Path(selected["bundle_directory"]).is_absolute()
        assert (recipient/"runtime"/selected["bundle_directory"]/"manifest.json").is_file()
        card=ArtifactStore(recipient/"audit").read(selected["model_card_ref"])
        assert card["service"]==selected["service"] and not card["activation_allowed"]
    assert ArtifactStore(recipient/"audit").read(predicted_ref)["rows"]==predicted
