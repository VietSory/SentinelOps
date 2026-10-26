"""Read public data as plain tables and report capabilities before modeling."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sentinelops.public_data.downloads import verify_bytes


def load_verified_manifest(path: Path) -> tuple[dict,dict[str,Path]]:
    blob=path.read_bytes()
    if path.stem!=hashlib.sha256(blob).hexdigest(): raise ValueError("download manifest checksum mismatch")
    manifest=json.loads(blob)
    if manifest.get("manifest_version")!="public-download/v1" or manifest.get("activation_allowed") is not False:
        raise ValueError("unsupported public dataset manifest")
    root=path.parent.parent.resolve();files={}
    for record in manifest["files"]:
        target=Path(record["path"]).resolve()
        if not target.is_relative_to(root): raise ValueError("manifest file escapes its public-data root")
        data=target.read_bytes()
        digest=verify_bytes(data,record["publisher_checksum"])
        if digest!=record["sha256"]: raise ValueError("download manifest/file checksum differs")
        relative=str(target.relative_to(root))
        if relative in files: raise ValueError("duplicate public dataset path")
        files[relative]=target
    return manifest,files


def audit_rcaeval(manifest_path: Path) -> dict:
    import numpy as np
    import pyarrow.parquet as pq
    manifest,files=load_verified_manifest(manifest_path)
    if manifest["dataset"]!="RCAEval": raise ValueError("not an RCAEval manifest")
    root=manifest_path.parent.parent/"rcaeval"/manifest["revision"]
    index=pq.read_table(root/"cases.parquet").to_pandas()
    if index["case"].duplicated().any(): raise ValueError("RCAEval case index has duplicate identities")
    counts={str(key):int(value) for key,value in index.groupby("dataset").size().items()}
    availability={str(key):{"cases":len(group),"cases_with_logs":int(group.has_logs.sum()),
        "cases_with_traces":int(group.has_traces.sum())} for key,group in index.groupby("dataset")}
    cases=[]
    for identifier in manifest["selection"]:
        selected=index[index.case==identifier]
        if len(selected)!=1: raise ValueError("downloaded case does not have exactly one index row")
        row=selected.iloc[0];directory=root/identifier
        metrics=pq.read_table(directory/"metrics.parquet").to_pandas()
        if "time" not in metrics or len(metrics.columns)!=len(set(metrics.columns)): raise ValueError("invalid metric time/columns")
        time=np.asarray(metrics["time"],dtype=float)
        if not np.isfinite(time).all() or np.any(np.diff(time)<=0): raise ValueError("metric time is not finite/strictly increasing")
        injection=int((directory/"inject_time.txt").read_text())
        if injection!=int(row.inject_time): raise ValueError("injection timestamp differs from case index")
        values=metrics.drop(columns="time").to_numpy(dtype=float)
        if np.isinf(values).any(): raise ValueError("infinite public metric value")
        detail={"case":identifier,"dataset":str(row.dataset),"ground_truth_service":str(row.root_cause_service),
            "fault":str(row.fault),"metric_rows":len(metrics),"metric_channels":len(metrics.columns)-1,
            "metric_null_cells":int(np.isnan(values).sum()),"normal_timestamp_rows":int((time<injection).sum()),
            "post_injection_timestamp_rows":int((time>=injection).sum()),"metric_columns":list(metrics.columns),
            "injection_time":injection,"time_start":float(time.min()),"time_end":float(time.max()),
            "trace_time_and_duration_units":"not established by this schema audit"}
        for modality in ("logs","traces"):
            file=directory/(modality+".parquet")
            detail[modality]={"present":file.exists()}
            if file.exists():
                table=pq.ParquetFile(file)
                detail[modality].update({"rows":table.metadata.num_rows,"columns":table.schema_arrow.names,
                    "types":{field.name:str(field.type) for field in table.schema_arrow}})
        cases.append(detail)
    return {"audit_version":"public-source-audit/v1","dataset":"RCAEval","revision":manifest["revision"],
        "declared_license":manifest["declared_license"],"index_cases":len(index),"case_counts":counts,
        "modality_availability":availability,"downloaded_cases":cases,"verified_files":len(files),
        "source_manifest":str(manifest_path),"activation_allowed":False,
        "limitations":["fault injection labels are not objective SLO onset or gradual precursor labels",
            "null metrics remain missing; no backward fill or zero fabrication",
            "source metric names/units and live RED feature parity require separate mapping",
            "selected cases audit format only; no training/holdout split or accuracy is claimed"]}


def audit_smd(manifest_path: Path) -> dict:
    import numpy as np
    manifest,files=load_verified_manifest(manifest_path)
    if manifest["dataset"]!="SMD": raise ValueError("not an SMD manifest")
    root=manifest_path.parent.parent/"smd"/manifest["revision"]/"ServerMachineDataset"
    machine=manifest["selection"][0]
    train=np.loadtxt(root/"train"/(machine+".txt"),delimiter=",",ndmin=2)
    test=np.loadtxt(root/"test"/(machine+".txt"),delimiter=",",ndmin=2)
    labels=np.loadtxt(root/"test_label"/(machine+".txt"),dtype=int,ndmin=1)
    if train.shape[1]!=38 or test.shape[1]!=38 or len(labels)!=len(test): raise ValueError("SMD feature/label shapes differ")
    if not np.isfinite(train).all() or not np.isfinite(test).all() or not np.isin(labels,[0,1]).all(): raise ValueError("invalid SMD values/labels")
    return {"audit_version":"public-source-audit/v1","dataset":"SMD","revision":manifest["revision"],
        "declared_license":manifest["declared_license"],"machine":machine,"train_rows":len(train),"test_rows":len(test),
        "feature_dimensions":38,"test_positive_points":int(labels.sum()),"verified_files":len(files),
        "source_manifest":str(manifest_path),"activation_allowed":False,
        "limitations":["one of 28 machines downloaded; this is not the full SMD benchmark",
            "38 channels lack a verified mapping to SentinelOps live RED features",
            "official train has no supplied point labels; nominal-training assumption is not independently certified",
            "binary test anomaly labels do not provide project SLO/gradual precursor labels",
            "no sampling interval/seconds-based detection latency is asserted by this audit"]}
