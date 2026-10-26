import json

import pytest

from scripts.summarize_telemetry_evidence import summarize
from sentinelops.telemetry.store import ArtifactStore


def make_report(tmp_path):
    store = ArtifactStore(tmp_path)
    raw = store.write("raw", {"fixture":True})
    observation = store.write("observations", {"quality":{"status":"valid"}})
    feature = store.write("features", {"service":"payment-service","payload":{"fixture":True}})
    manifest = {"source_profile":"fixture/v1", "raw_refs":[raw], "observation_refs":[observation],
                "feature_refs":[feature], "quarantine_refs":[]}
    manifest["manifest_ref"] = store.write("manifests", manifest)
    report = {"status":"PASS","scope":"synthetic test fixture", "manifest":manifest,
              "details":{"counters":{"200":1}},"evidence_refs":[raw],"commit":"legacy-end-only"}
    path = tmp_path/"report.json"
    path.write_text(json.dumps(report))
    return path


def test_summary_verifies_content_and_does_not_invent_legacy_start_commit(tmp_path):
    result = summarize(make_report(tmp_path))
    assert result["commit_at_start"] is None
    assert result["commit_at_report"] == "legacy-end-only"
    assert result["eligible_features_by_service"] == {"payment-service":1}
    assert result["raw_archive_required_for_release"] is True
    assert set(result["evidence_capture_clocks"].values()) == {None}


def test_summary_preserves_capture_clocks_and_rejects_non_utc_metadata(tmp_path):
    path = make_report(tmp_path)
    report = json.loads(path.read_text())
    store = ArtifactStore(tmp_path)
    raw = store.write("raw", {"kind":"trace", "collected_at":"2026-10-26T00:00:00Z"})
    report["evidence_refs"] = [raw]; path.write_text(json.dumps(report))
    assert summarize(path)["evidence_capture_clocks"] == {raw:"2026-10-26T00:00:00Z"}
    raw = store.write("raw", {"kind":"trace", "collected_at":"2026-10-26T00:00:00"})
    report["evidence_refs"] = [raw]; path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="UTC"):
        summarize(path)


def test_summary_rejects_tampered_report_or_raw_evidence(tmp_path):
    path = make_report(tmp_path)
    report = json.loads(path.read_text())
    report["manifest"]["source_profile"] = "different/v1"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="manifest differs"):
        summarize(path)
    path = make_report(tmp_path)
    report = json.loads(path.read_text())
    raw = tmp_path/report["manifest"]["raw_refs"][0].removeprefix("artifact://")
    raw.write_text("corrupt")
    with pytest.raises(ValueError, match="checksum"):
        summarize(path)
