from concurrent.futures import ThreadPoolExecutor

import pytest

from sentinelops.telemetry.store import ArtifactStore


def test_identical_object_retry_is_read_only_and_preserves_identity(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    value = {"example": [1, 2, 3], "mode": "fixture"}
    ref = store.write("raw", value)
    path = tmp_path / ref.removeprefix("artifact://")
    before = path.stat()
    monkeypatch.setattr("sentinelops.telemetry.store.tempfile.NamedTemporaryFile",
                        lambda *a, **k: pytest.fail("identical existing content must not create/fsync a temporary file"))
    assert store.write("raw", value) == ref
    assert store.read(ref) == value
    assert (path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_size) == (before.st_ino, before.st_mtime_ns, before.st_size)


def test_corrupt_existing_object_retry_never_silently_repairs_cache(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    ref = store.write("signals", {"fixture": True})
    path = tmp_path / ref.removeprefix("artifact://")
    path.write_bytes(b"corrupt fixture")
    monkeypatch.setattr("sentinelops.telemetry.store.tempfile.NamedTemporaryFile",
                        lambda *a, **k: pytest.fail("corrupt existing content must fail before filesystem mutation"))
    with pytest.raises(ValueError, match="corrupt"):
        store.write("signals", {"fixture": True})
    assert path.read_bytes() == b"corrupt fixture"


def test_concurrent_first_writes_and_retries_publish_one_complete_object(tmp_path):
    store = ArtifactStore(tmp_path)
    value = {"fixture": list(range(100))}
    with ThreadPoolExecutor(max_workers=8) as executor:
        refs = list(executor.map(lambda _: store.write("observations", value), range(32)))
    assert len(set(refs)) == 1 and store.read(refs[0]) == value
    assert len(list(tmp_path.rglob("*.json"))) == 1
    assert sorted(path.name for path in (tmp_path / "observations").iterdir()) == [refs[0].rsplit("/", 1)[1]]
