"""Checksum-pinned single-file model upload; default is local validation only.

The release owner supplies an archive AND its independently trusted SHA-256.
No extraction, deserialization, model approval, promotion or activation occurs.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def aws(args, *operation):
    result = subprocess.run(["aws", "--region", args.region, "--no-cli-pager", "--output", "json",
                             *operation], capture_output=True, text=True, timeout=900)
    if result.returncode:
        raise RuntimeError(f"AWS CLI failed (exit {result.returncode}); inspect account/permissions/immutable-key collision")
    return json.loads(result.stdout)


def upload(args):
    path = args.bundle.absolute()
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 1024 ** 3:
        raise ValueError("supply a regular nonempty bundle archive <= 1 GiB; no symlinks")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", path.name):
        raise ValueError("bundle basename must be a simple portable filename")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", args.release):
        raise ValueError("invalid release prefix")
    if not re.fullmatch(r"[0-9a-f]{64}", args.expected_sha256):
        raise ValueError("expected SHA-256 must be the full lowercase release-owner checksum")
    actual = sha256(path)
    if actual != args.expected_sha256:
        raise ValueError("local bundle checksum differs; no AWS call made")
    key = f"models/{args.release}/{actual}/{path.name}"
    receipt = {"bucket": args.bucket, "key": key, "sha256": actual, "activation_allowed": False}
    if not args.execute:
        return dict(receipt, status="LOCAL_CHECKSUM_PASS_NO_UPLOAD")
    if aws(args, "sts", "get-caller-identity")["Account"] != args.account_id:
        raise ValueError("AWS caller account differs from owner account")
    owner = ["--expected-bucket-owner", args.account_id]
    if aws(args, "s3api", "get-bucket-versioning", "--bucket", args.bucket, *owner).get("Status") != "Enabled":
        raise ValueError("artifact bucket must have versioning Enabled")
    encryption = aws(args, "s3api", "get-bucket-encryption", "--bucket", args.bucket, *owner)
    rules = encryption["ServerSideEncryptionConfiguration"]["Rules"]
    if len(rules) != 1 or rules[0]["ApplyServerSideEncryptionByDefault"].get("SSEAlgorithm") != "aws:kms":
        raise ValueError("artifact bucket default encryption must be SSE-KMS")
    checksum = base64.b64encode(bytes.fromhex(actual)).decode()
    # Use the reviewed bucket default KMS key; no output exposing it exists in outputs.tf.
    result = aws(args, "s3api", "put-object", "--bucket", args.bucket, "--key", key,
                 "--body", str(path), "--checksum-algorithm", "SHA256", "--checksum-sha256", checksum,
                 "--if-none-match", "*", *owner)
    version = result.get("VersionId")
    if (not version or version == "null" or result.get("ChecksumSHA256") != checksum
        or result.get("ServerSideEncryption") != "aws:kms"):
        raise RuntimeError("upload response lacks version/checksum/KMS evidence; do not activate")
    head = aws(args, "s3api", "head-object", "--bucket", args.bucket, "--key", key,
               "--version-id", version, "--checksum-mode", "ENABLED", *owner)
    if head.get("ChecksumSHA256") != checksum or head.get("ContentLength") != path.stat().st_size:
        raise RuntimeError("versioned HEAD checksum/length mismatch; do not activate")
    # Re-download the exact version and hash actual bytes, not ETag or user metadata.
    with tempfile.TemporaryDirectory(prefix="sentinelops-model-readback-") as directory:
        downloaded = Path(directory) / "bundle"
        aws(args, "s3api", "get-object", "--bucket", args.bucket, "--key", key,
            "--version-id", version, "--checksum-mode", "ENABLED", *owner, str(downloaded))
        if sha256(downloaded) != actual:
            raise RuntimeError("downloaded bytes differ; do not activate")
    return dict(receipt, version_id=version, status="UPLOAD_AND_READBACK_PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--release", default="de38fc6b", help="label only, not model approval")
    parser.add_argument("--bucket", required=True, help="Terraform artifact_bucket")
    parser.add_argument("--account-id", required=True, help="Owner account ID; not a Terraform output")
    parser.add_argument("--region", default="ap-southeast-1")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{12}", args.account_id):
        parser.error("account-id must have 12 digits")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", args.bucket):
        parser.error("invalid artifact bucket name")
    print(json.dumps(upload(args), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
