"""Small immutable, content-addressed local object store."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

KINDS = {"raw", "observations", "features", "manifests", "quarantine"}


class ArtifactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def write(self, kind: str, value: dict[str, Any]) -> str:
        if kind not in KINDS:
            raise ValueError("unsupported artifact kind")
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        digest = hashlib.sha256(data).hexdigest()
        target = self.root / kind / f"{digest}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            try:
                os.link(temporary, target)  # Atomic create, never overwrite.
            except FileExistsError:
                if target.read_bytes() != data:
                    raise ValueError("immutable artifact is corrupt")
        finally:
            temporary.unlink(missing_ok=True)
        return f"artifact://{kind}/{digest}.json"

    def read(self, reference: str) -> dict[str, Any]:
        match = re.fullmatch(r"artifact://([a-z]+)/([0-9a-f]{64})\.json", reference)
        if not match or match[1] not in KINDS:
            raise ValueError("invalid artifact reference")
        path = self.root / match[1] / f"{match[2]}.json"
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != match[2]:
            raise ValueError("artifact checksum mismatch")
        return json.loads(data)
