"""Frozen SMD IF/control benchmark; never implicitly activates a live model."""
from __future__ import annotations

from datetime import datetime,timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import subprocess
import time

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score,confusion_matrix,precision_recall_fscore_support,roc_auc_score
from sklearn.preprocessing import RobustScaler

from sentinelops.public_data.audit import load_verified_manifest,require_verified_file


def valid_matrix(values):
    array=np.asarray(values,dtype=float)
    if array.ndim!=2 or array.shape[1]!=38 or not np.isfinite(array).all():
        raise ValueError("SMD input must have exactly 38 finite numeric channels")
    return array


def control_scores(bundle,values):
    values=valid_matrix(values)
    return {"isolation_forest":-bundle["isolation_forest"].score_samples(bundle["scaler"].transform(values)),
        "median_mad":np.max(np.abs(values-bundle["median"])/bundle["mad_scale"],axis=1)}


def fit_controls(train,protocol):
    train=valid_matrix(train)
    fraction=protocol["fit_fraction_of_official_train"]
    budget=protocol["calibration_exceedance_budget"]
    if (not 0<fraction<1 or not np.isclose(fraction+protocol["calibration_fraction_of_official_train"],1.)
        or not 0<budget<.5 or protocol["point_adjustment"] is not False or protocol["activation_allowed"] is not False):
        raise ValueError("invalid frozen research protocol")
    boundary=int(len(train)*fraction)
    fit,cal=train[:boundary],train[boundary:]
    if min(len(fit),len(cal))<256:
        raise ValueError("SMD benchmark requires at least 256 fit/calibration rows")
    scaler=RobustScaler().fit(fit)
    forest=IsolationForest(**protocol["isolation_forest"],random_state=protocol["random_seed"],contamination="auto")
    forest.fit(scaler.transform(fit))
    median=np.median(fit,axis=0)
    scale=np.maximum(1.4826*np.median(np.abs(fit-median),axis=0),protocol["statistical_baseline"]["scale_floor"])
    bundle={"isolation_forest":forest,"scaler":scaler,"median":median,"mad_scale":scale,
            "feature_set_version":"smd-raw38/v1","ordered_features":[f"smd_channel_{index:02d}" for index in range(38)],
            "activation_allowed":False}
    scores=control_scores(bundle,cal)
    thresholds={key:float(np.quantile(values,1-budget,method="higher")) for key,values in scores.items()}
    bundle["thresholds"]=thresholds
    split={"official_train_rows":len(train),"fit_rows":len(fit),"calibration_rows":len(cal),
        "fit_index_range":[0,boundary],"calibration_index_range":[boundary,len(train)],
        "calibration_label_status":"unlabelled nominal training distribution; not certified all-normal",
        "calibration_exceedance":{key:float(np.mean(values>thresholds[key])) for key,values in scores.items()}}
    return bundle,split


def point_metrics(labels,scores,threshold):
    labels=np.asarray(labels)
    scores=np.asarray(scores,dtype=float)
    if labels.ndim!=1 or not len(labels) or scores.shape!=labels.shape or not np.isin(labels,[0,1]).all() or not np.isfinite(scores).all() or not math.isfinite(threshold):
        raise ValueError("invalid point evaluation arrays")
    predictions=scores>threshold
    tn,fp,fn,tp=confusion_matrix(labels,predictions,labels=[0,1]).ravel()
    precision,recall,f1,_=precision_recall_fscore_support(labels,predictions,average="binary",zero_division=0)
    both=len(np.unique(labels))==2
    return {"test_points":len(labels),"positive_points":int(tp+fn),"normal_points":int(tn+fp),
        "tp":int(tp),"fp":int(fp),"fn":int(fn),"tn":int(tn),
        "precision":float(precision),"recall":float(recall),"f1":float(f1),
        "average_precision":float(average_precision_score(labels,scores)) if np.any(labels) else None,
        "roc_auc":float(roc_auc_score(labels,scores)) if both else None,
        "false_positives_per_1000_normal_points":float(1000*fp/(tn+fp)) if tn+fp else None,
        "threshold":threshold,"point_adjustment":False}


def _write_json(path,value):
    with path.open("x") as stream:
        json.dump(value,stream,indent=2,allow_nan=False);stream.write("\n");stream.flush();os.fsync(stream.fileno())


def run_benchmark(manifest_path: Path,protocol_path: Path,output: Path,*,project: Path) -> dict:
    manifest,files=load_verified_manifest(manifest_path)
    protocol=json.loads(protocol_path.read_text())
    if manifest["dataset"]!="SMD" or manifest["selection"]!=[protocol["machine"]]:
        raise ValueError("source selection differs from the frozen benchmark protocol")
    commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=project,text=True).strip()
    dirty=bool(subprocess.check_output(["git","status","--porcelain"],cwd=project,text=True).strip())
    if dirty: raise ValueError("commit the training implementation/protocol before scoring the test set")
    output.mkdir(parents=True,exist_ok=False)
    directory=manifest_path.parent.parent/"smd"/manifest["revision"]/"ServerMachineDataset"
    machine=protocol["machine"]
    train_path=require_verified_file(files,directory/"train"/(machine+".txt"))
    test_path=require_verified_file(files,directory/"test"/(machine+".txt"))
    label_path=require_verified_file(files,directory/"test_label"/(machine+".txt"))
    train=valid_matrix(np.loadtxt(train_path,delimiter=",",ndmin=2))
    started=time.monotonic()
    bundle,split=fit_controls(train,protocol)
    fit_seconds=time.monotonic()-started
    model_path=output/"research-model.joblib"
    joblib.dump(bundle,model_path,compress=3)
    model_hash=hashlib.sha256(model_path.read_bytes()).hexdigest()
    frozen={"model_manifest_version":"public-research-model/v1","source_commit":commit,"dirty_worktree":False,
        "dataset":"SMD","dataset_revision":manifest["revision"],"dataset_manifest_sha256":manifest_path.stem,
        "machine":machine,"protocol":protocol,"protocol_sha256":hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "feature_set_version":bundle["feature_set_version"],"ordered_features":bundle["ordered_features"],
        "split":split,"thresholds":bundle["thresholds"],"fit_seconds":fit_seconds,
        "frozen_at":datetime.now(timezone.utc).isoformat(),"model_sha256":model_hash,
        "library_versions":{name:importlib.metadata.version(name) for name in ("numpy","scipy","scikit-learn","joblib")},
        "activation_allowed":False,"serving_compatible":False,
        "compatibility_reason":"38 anonymous SMD channels do not match the five live http-red-5m/v1 features"}
    _write_json(output/"frozen-model.json",frozen)
    print("MODEL AND CALIBRATION FROZEN BEFORE TEST EVALUATION",flush=True)
    # Test values/labels are parsed only after the immutable model/threshold file
    # has been persisted. Download hashes were verified earlier, not used to fit.
    test=valid_matrix(np.loadtxt(test_path,delimiter=",",ndmin=2))
    labels=np.loadtxt(label_path,ndmin=1)
    if len(test)!=len(labels): raise ValueError("test label count differs from test rows")
    scores=control_scores(bundle,test)
    metrics={key:point_metrics(labels,values,bundle["thresholds"][key]) for key,values in scores.items()}
    np.savez_compressed(output/"test-predictions.npz",labels=labels,**scores)
    result={"status":"completed","scope":"one-machine external public benchmark; no live deployment or G3 promotion",
        "frozen_model":frozen,"metrics":metrics,"evaluation_at":datetime.now(timezone.utc).isoformat(),
        "xgboost_status":"not trained: no verified precursor/SLO labels in SMD",
        "selection_reason":"report both frozen methods; no winner or threshold is chosen using official test labels"}
    _write_json(output/"report.json",result)
    return result
