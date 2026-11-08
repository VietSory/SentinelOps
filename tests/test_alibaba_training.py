"""ADR-016 causal relative windows and breach labels on synthetic minute series."""
import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from sentinelops.ml.alibaba_training import load_config, service_windows, split_of


def series(rt):
    return pd.DataFrame({'rt': rt, 'rate': [1.0] * len(rt)}, index=range(len(rt)))


def test_breach_onset_labels_only_windows_before_the_onset():
    config = load_config()
    rt = [10.0] * 80 + [30.0] * 10 + [10.0] * 30   # onset at minute 80
    rows = {r['minute']: r for r in service_windows(series(rt), config)}
    assert rows[79]['risk_label'] == 1 and rows[79]['onset_minute'] == 80
    assert rows[75]['risk_label'] == 1                  # onset within (t, t+300s]
    assert rows[74]['risk_label'] is None               # onset just past the horizon falls in the tail: excluded
    assert rows[72]['risk_label'] == 0                  # fully observed, no breach through horizon + tail
    assert rows[80]['risk_label'] is None               # breach inside the window: neither class
    assert rows[60]['healthy'] and not rows[79]['healthy']
    assert rows[70]['features'][0] == pytest.approx(0.0)


def test_features_use_only_past_minutes():
    config = load_config()
    base = [10.0] * 80
    a = {r['minute']: r['features'] for r in service_windows(series(base + [10.0] * 20), config)}
    b = {r['minute']: r['features'] for r in service_windows(series(base + [50.0] * 20), config)}
    assert a[79] == b[79]            # a future change cannot alter an earlier window's features
    assert a[85] != b[85]


def test_baseline_needs_minimum_history_and_split_is_stable_by_identity():
    config = load_config()
    rows = service_windows(series([10.0] * 9), config)
    assert rows == []                # fewer than window + min_minutes of history
    assert split_of('MS_1') == split_of('MS_1') and {split_of(f'MS_{i}') for i in range(200)} == {'train', 'calibration', 'evaluation'}
