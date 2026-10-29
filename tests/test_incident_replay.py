import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from sentinelops.workers.incident_replay import MAX_OBJECT_BYTES, ReplayStore, bounded_bytes, replay


@pytest.mark.parametrize("body", [b'{"same":1,"same":2}', b'{"value":NaN}', b'[]'])
def test_hash_valid_json_cannot_hide_duplicate_keys_nonfinite_values_or_wrong_shape(tmp_path, body):
    digest = hashlib.sha256(body).hexdigest()
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests" / (digest + ".json")).write_bytes(body)
    with pytest.raises(ValueError, match="JSON|object"):
        ReplayStore(tmp_path).read("artifact://manifests/" + digest + ".json")


def test_oversized_sparse_capture_is_rejected_before_json_decoding(tmp_path):
    path = tmp_path / "large.json"
    with path.open("wb") as stream:
        stream.seek(MAX_OBJECT_BYTES)
        stream.write(b" ")
    with pytest.raises(ValueError, match="byte budget"):
        bounded_bytes(path)


def test_invalid_trusted_selector_is_rejected_before_reading_missing_input(tmp_path):
    missing = tmp_path / "absent"
    with pytest.raises(ValueError, match="SHA256"):
        replay(missing, expected_incident_snapshot_sha256="not-a-hash")
    assert not missing.exists()


def test_optimized_cli_cannot_report_pass_with_acceptance_assertions_disabled(tmp_path):
    result = subprocess.run([sys.executable, "-O", "-m", "scripts.replay_incident_worker",
        "--run", str(tmp_path / "absent"), "--output", str(tmp_path / "result.json")],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
    assert result.returncode != 0 and "assertions enabled" in result.stderr
    assert not (tmp_path / "result.json").exists()
