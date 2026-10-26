from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import httpx
import pytest

from sentinelops.contracts import validate_payload
from sentinelops.public_data.gamma import METRICS, download_gamma
from sentinelops.public_data.resources import build_resource_windows, metric_bucket, resource_window, validate_series
from test_gamma_downloads import EXPERIMENT, source_files, transport


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "configs/gamma_resource_profile.json"


def profile():
    return json.loads(PROFILE.read_text())


def series():
    return {metric: [[time, 100. if metric == "container_memory_usage_bytes" else float(time * rate)]
                     for time in range(0, 1201, 15)]
            for metric, rate in zip(METRICS, (2, 5, 7, 0))}


def test_rates_use_counter_deltas_and_covered_time_instead_of_counter_levels():
    result = metric_bucket([(0, 100), (15, 130), (30, 160), (45, 190), (60, 220)],
                           0, 60, counter=True, max_gap=30, max_age=30)
    assert result["value"] == 2. and result["coverage"] == 1.
    assert result["max_input_time"] == 60


def test_reset_and_unknown_gap_intervals_are_excluded_not_counted_as_zero_load():
    reset = metric_bucket([(0, 100), (15, 10), (30, 40)], 0, 60, counter=True, max_gap=30, max_age=30)
    assert reset["value"] == 2. and reset["covered_seconds"] == 15
    assert "counter-reset" in reset["reasons"]
    gap = metric_bucket([(0, 10), (60, 130)], 0, 60, counter=True, max_gap=30, max_age=30)
    assert gap["value"] is None and gap["coverage"] == 0


def test_gauge_hold_is_bounded_and_future_samples_do_not_refresh_an_outage():
    point = metric_bucket([(0, 100), (61, 900)], 0, 60, counter=False, max_gap=30, max_age=15)
    assert point["covered_seconds"] == 15 and point["value"] == 100
    assert point["max_input_time"] == 0


def test_future_values_cannot_change_present_features_coverage_or_reset_flags():
    original = series()
    altered = deepcopy(original)
    for values in altered.values():
        for row in values:
            if row[0] > 600:
                row[1] = 1e15 if row[0] % 30 else 0
    expected = resource_window(original, 600, profile())
    assert resource_window(altered, 600, profile()) == expected
    truncated = {metric: [row for row in values if row[0] <= 600] for metric, values in original.items()}
    assert resource_window(truncated, 600, profile()) == expected
    assert expected["features"]["cpu_cores_mean_5m"] == 2.
    assert expected["features"]["memory_bytes_mean_5m"] == 100.
    assert expected["features"]["cpu_cores_slope_5m"] == 0
    assert expected["features"]["network_receive_bytes_per_second_mean_5m"] == 5.


def test_missing_required_observations_do_not_become_a_healthy_numeric_vector():
    values = series()
    values["container_cpu_usage_seconds_total"] = []
    result = resource_window(values, 600, profile())
    assert result["eligible_for_features"] is False and result["features"] is None
    assert result["quality"]["valid_buckets"] == 0


@pytest.mark.parametrize("value", [[[1, 1], [1, 2]], [[1, float("nan")]], [[1, -1]]])
def test_malformed_or_missing_resource_data_is_not_repaired_from_future_samples(value):
    with pytest.raises(ValueError):
        validate_series(value)


def source_fixture(*, truth_marker):
    files = source_files()
    files["meta-data/meta-data/placement.csv"] = b"service,node\n"
    files[f"raw_dataset/{EXPERIMENT}/args.txt"] = json.dumps({"bottlenecked_nodes": [truth_marker], "phases": [999]}).encode()
    for metric, values in series().items():
        files[f"raw_dataset/{EXPERIMENT}/prom_metrics/service_{metric}"] = json.dumps(
            [[1692000000 + time, str(value)] for time, value in values]).encode()
    return files


def test_native_labels_and_schedules_cannot_enter_resource_feature_values(tmp_path):
    outputs = []
    for index, marker in enumerate(("truth-A", "truth-B")):
        with httpx.Client(transport=transport(source_fixture(truth_marker=marker))) as client:
            acquisition = download_gamma(client, tmp_path / str(index), experiments=[EXPERIMENT], workers=1)
        destination = tmp_path / f"frames-{index}"
        report = build_resource_windows(Path(acquisition["manifest_path"]), PROFILE, destination)
        rows = [json.loads(line) for line in (destination / "resource-windows.jsonl").read_text().splitlines()]
        assert report["rows"] == report["eligible_feature_rows"] == 11
        assert report["training_ready"] is False and report["activation_allowed"] is False
        outputs.append([row["features"] for row in rows])
        row = deepcopy(rows[0])
        row["max_input_observed_at"] = "2099-01-01T00:00:00Z"
        with pytest.raises(ValueError, match="after its cutoff"):
            validate_payload("research-resource-window.schema.json", row)
        assert all(datetime.fromisoformat(row["window_start"].replace("Z", "+00:00")).timestamp() >= 1692000300 for row in rows)
    assert outputs[0] == outputs[1]
