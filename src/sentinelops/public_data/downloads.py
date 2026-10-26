"""Pinned, checksum-verified data-only downloads from the original publishers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import tempfile

import httpx

from sentinelops.public_data.policy import load_policy, require_historical_smd


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()


def safe_relative(name: str) -> Path:
    value=PurePosixPath(name)
    if value.is_absolute() or not value.parts or any(part in (".","..") for part in value.parts) or any(char in name for char in ("\\",":","\x00")):
        raise ValueError("invalid relative dataset path")
    return Path(*value.parts)


def verify_bytes(data: bytes,expected: dict) -> str:
    if len(data)!=expected["size"]:
        raise ValueError("downloaded dataset size differs from publisher metadata")
    digest=hashlib.sha256(data).hexdigest()
    if expected.get("sha256"):
        if digest!=expected["sha256"]:
            raise ValueError("downloaded dataset SHA256 differs from publisher metadata")
    elif git_blob_sha(data)!=expected["git_blob"]:
        raise ValueError("downloaded dataset Git blob differs from publisher metadata")
    return digest


def fetch_file(client,url: str,target: Path,expected: dict,*,max_bytes: int) -> dict:
    if not 0<=expected["size"]<=max_bytes:
        raise ValueError("file exceeds the declared download budget")
    if target.exists():
        data=target.read_bytes()
    else:
        chunks=[];size=0
        with client.stream("GET",url) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                size+=len(chunk)
                if size>min(max_bytes,expected["size"]):
                    raise ValueError("download stream exceeds publisher size/budget")
                chunks.append(chunk)
        data=b"".join(chunks)
    digest=verify_bytes(data,expected)
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as stream:
            temporary=Path(stream.name);stream.write(data);stream.flush();os.fsync(stream.fileno())
        try:
            try: os.link(temporary,target)
            except FileExistsError:
                if target.read_bytes()!=data: raise ValueError("immutable public dataset path already has different contents")
        finally: temporary.unlink(missing_ok=True)
    print(f"VERIFIED {target.name} bytes={len(data)}",flush=True)
    return {"path":str(target),"source_url":url,"size":len(data),"sha256":digest,
            "publisher_checksum":expected}


def _json(client,url):
    response=client.get(url);response.raise_for_status();return response.json()


def _save_manifest(root,manifest):
    encoded=json.dumps(manifest,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    path=root/"manifests"/(hashlib.sha256(encoded).hexdigest()+".json")
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.read_bytes()!=encoded: raise ValueError("download manifest path is corrupt")
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as stream:
            temporary=Path(stream.name);stream.write(encoded);stream.flush();os.fsync(stream.fileno())
        try:
            try: os.link(temporary,path)
            except FileExistsError:
                if path.read_bytes()!=encoded: raise ValueError("download manifest path is corrupt")
        finally: temporary.unlink(missing_ok=True)
    return {**manifest,"manifest_path":str(path)}


def download_rcaeval(client,config: dict,root: Path,*,cases: list[str] | None = None,max_bytes: int = 256_000_000) -> dict:
    repository,revision=config["repository"],config["revision"]
    source=load_policy()["sources"]["rcaeval"]
    if repository!=source["publisher"] or revision!=source["revision"]:
        raise ValueError("RCAEval catalog differs from the frozen training-data/v1 evaluation source")
    selected=sorted(set(cases or config["default_cases"]))
    if not selected or any(not re.fullmatch(r"re[123](ob|ss|tt)_[a-zA-Z0-9_-]+",case) for case in selected):
        raise ValueError("invalid RCAEval case selection")
    meta=_json(client,f"https://huggingface.co/api/datasets/{repository}/revision/{revision}?blobs=true")
    if meta.get("sha")!=revision or str(meta.get("cardData",{}).get("license","")).lower()!="mit":
        raise ValueError("RCAEval revision/license metadata differs from the audited catalog")
    files=[item for item in meta["siblings"] if item["rfilename"] in ("README.md","cases.parquet")
           or item["rfilename"].split('/')[0] in selected]
    if any(not any(item["rfilename"]==case+"/metrics.parquet" for item in files) for case in selected):
        raise ValueError("selected RCAEval case has no published metrics")
    if sum(item["size"] for item in files)>max_bytes: raise ValueError("RCAEval selection exceeds download budget")
    destination=root/"rcaeval"/revision
    records=[]
    for item in files:
        name=item["rfilename"]
        if Path(name).suffix not in (".md",".txt",".parquet"): raise ValueError("unexpected non-data RCAEval file")
        expected={"size":item["size"],"sha256":item["lfs"]["sha256"]} if item.get("lfs") else {"size":item["size"],"git_blob":item["blobId"]}
        records.append(fetch_file(client,f"https://huggingface.co/datasets/{repository}/resolve/{revision}/{name}",
                                  destination/safe_relative(name),expected,max_bytes=max_bytes))
    return _save_manifest(root,{"manifest_version":"public-download/v1","dataset":"RCAEval","revision":revision,
        "homepage":config["homepage"],"declared_license":config["declared_license"],"selection":selected,"files":records,
        "serving_compatible":False,"activation_allowed":False})


def download_smd(client,config: dict,root: Path,*,machine: str | None = None,max_bytes: int = 64_000_000,historical_reproduction: bool = False) -> dict:
    machine=machine or config["default_machine"]
    require_historical_smd(machine,acknowledged=historical_reproduction)
    if not re.fullmatch(r"machine-[123]-[1-9][0-9]*",machine): raise ValueError("invalid SMD machine ID")
    repository,revision=config["repository"],config["revision"]
    destination=root/"smd"/revision
    records=[];total=0
    for relative in ("LICENSE","README.md",f"ServerMachineDataset/train/{machine}.txt",
                     f"ServerMachineDataset/test/{machine}.txt",f"ServerMachineDataset/test_label/{machine}.txt"):
        meta=_json(client,f"https://api.github.com/repos/{repository}/contents/{relative}?ref={revision}")
        if meta.get("type")!="file" or meta.get("path")!=relative: raise ValueError("unexpected SMD metadata")
        total+=meta["size"]
        if total>max_bytes: raise ValueError("SMD selection exceeds download budget")
        records.append(fetch_file(client,f"https://raw.githubusercontent.com/{repository}/{revision}/{relative}",
            destination/safe_relative(relative),{"size":meta["size"],"git_blob":meta["sha"]},max_bytes=max_bytes))
    return _save_manifest(root,{"manifest_version":"public-download/v1","dataset":"SMD","revision":revision,
        "homepage":config["homepage"],"declared_license":config["declared_license"],"selection":[machine],"files":records,
        "serving_compatible":False,"activation_allowed":False})
