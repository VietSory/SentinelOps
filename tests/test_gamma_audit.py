from pathlib import Path

import httpx
import pytest

pytest.importorskip("pandas")

from sentinelops.public_data.gamma import META_FILES, download_gamma
from sentinelops.public_data.gamma_audit import audit_gamma
from test_gamma_downloads import EXPERIMENT, source_files, transport


def measured_fixture(*, valid_labels=True):
    files = source_files()
    for name in META_FILES:
        files[name] = b"rpc,service\n"
    labels = "1" if valid_labels else "0.5"
    table = ("0_latency,0_start,window_id,0_container_cpu_usage_seconds_total,0_label_RPC,label_trace,trace_id\n"
             "10,2000000,w0,1,0,0,t0\n"
             f"20,1000000,w1,1,{labels},1,t1\n").encode()
    for name in files:
        if name.startswith("processed_dataset/"):
            files[name] = table
        if "/prom_metrics/" in name:
            files[name] = b'[[1,"1"],[2,"0.5"]]'
    return files


def test_audit_reports_out_of_order_data_and_resets_without_certifying_labels(tmp_path):
    with httpx.Client(transport=transport(measured_fixture())) as client:
        acquisition = download_gamma(client, tmp_path, experiments=[EXPERIMENT], workers=1)
    report = audit_gamma(Path(acquisition["manifest_path"]))
    experiment = report["experiments"][0]
    assert experiment["raw_metrics"][0]["counter_reset_count"] == 1
    assert all(table["decreasing_root_time_transitions"] == 1 for table in experiment["processed_tables"])
    assert all(table["normal_trace_labels"] == table["anomalous_trace_labels"] == 1 for table in experiment["processed_tables"])
    assert all(value is False for value in report["training_ready"].values())
    assert report["activation_allowed"] is False


def test_nonbinary_source_labels_cannot_silently_become_supervised_targets(tmp_path):
    with httpx.Client(transport=transport(measured_fixture(valid_labels=False))) as client:
        acquisition = download_gamma(client, tmp_path, experiments=[EXPERIMENT], workers=1)
    with pytest.raises(ValueError, match="labels or RPC time"):
        audit_gamma(Path(acquisition["manifest_path"]))


def test_ambiguous_source_mapping_is_reported_without_repairing_or_certifying_it(tmp_path):
    files = measured_fixture()
    files["meta-data/meta-data/compose_ms_rpc_map.csv"] = b"rpc,service (note, missing span)\n"
    with httpx.Client(transport=transport(files)) as client:
        acquisition = download_gamma(client, tmp_path, experiments=[EXPERIMENT], workers=1)
    report = audit_gamma(Path(acquisition["manifest_path"]))
    assert report["rpc_mappings"]["compose"]["ambiguous_mapping_rows"] == [{"zero_based_row": 0, "parsed_fields": 3}]
    assert report["training_ready"]["isolation_forest"] is False
