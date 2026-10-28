import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from scripts.replay_health_worker import replay
from sentinelops.telemetry.core import stable_id


def test_wrong_snapshot_checksum_is_rejected_before_metadata_or_objects_are_opened(tmp_path):
    (tmp_path / "database-snapshot.json").write_text("not trusted JSON")
    with pytest.raises(ValueError, match="checksum"):
        asyncio.run(replay(tmp_path, expected_snapshot_sha256="0" * 64))


@pytest.mark.parametrize("problem", ["checkpoint", "foreign-stream"])
def test_replay_cannot_mix_source_streams_or_accept_a_stale_pointer(tmp_path, monkeypatch, problem):
    identity = {"fixture": True}
    identifier = stable_id(identity)
    reference = "artifact://manifests/" + "a" * 64 + ".json"
    snapshot = {"snapshot_version": "health-ledger-snapshot/v1",
                "stream": {"stream_id": identifier, "identity": identity,
                           "last_bucket_end": "2026-10-28T00:00:00Z", "last_batch_ref": reference},
                "batches": [{"stream_id": identifier, "bucket_end": "2026-10-28T00:00:00Z", "manifest_ref": reference}],
                "signals": [{"stream_id": identifier}]}
    if problem == "checkpoint": snapshot["stream"]["last_batch_ref"] = "changed"
    else: snapshot["signals"][0]["stream_id"] = "other"
    (tmp_path / "database-snapshot.json").write_text(json.dumps(snapshot))
    (tmp_path / "running.json").write_text(json.dumps({"stream_id": identifier}))
    monkeypatch.setattr("scripts.replay_health_worker.verify", AsyncMock(side_effect=AssertionError("invalid ledger reached object replay")))
    with pytest.raises(ValueError): asyncio.run(replay(tmp_path))
