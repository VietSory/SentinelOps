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


@pytest.mark.parametrize('seed', range(6))
def test_vectorised_transform_equals_the_reference(seed):
    from sentinelops.ml.alibaba_training import service_windows_fast
    config = load_config()
    rng = np.random.default_rng(seed)
    n = 200
    rt = rng.lognormal(2.5, 0.3, n)
    rt[rng.integers(0, n, 8)] *= rng.uniform(2.5, 6, 8)              # spikes → breaches
    rate = rng.lognormal(0, 0.2, n)
    rt[rng.integers(0, n, 10)] = np.nan                              # gaps
    rate[rng.integers(0, n, 6)] = np.nan
    index = np.sort(rng.choice(np.arange(n + 30), n, replace=False))  # missing minutes
    frame = pd.DataFrame({'rt': rt, 'rate': rate}, index=index)
    ref, fast = service_windows(frame, config), service_windows_fast(frame, config)
    assert [r['minute'] for r in ref] == [r['minute'] for r in fast]
    for a, b in zip(ref, fast):
        assert a['healthy'] == b['healthy'] and a['onset_minute'] == b['onset_minute']
        assert np.allclose(a['features'], b['features'][:5], rtol=1e-9, atol=1e-12)
        assert b['features'][5] == pytest.approx(np.log(b['baseline_rt'] / config['serving']['breach_latency_ms']))
        assert b['baseline_rt'] > 0


def test_each_breach_multiplier_has_its_own_onset_labels():
    from sentinelops.ml.alibaba_training import service_windows_fast
    config = load_config()
    multipliers = config['label']['breach_multipliers']
    rt = [10.0] * 80 + [50.0] * 10 + [10.0] * 30      # 5x baseline from minute 80: breaches 2x and 4x only
    rows = {r['minute']: r for r in service_windows_fast(series(rt), config)}
    by = dict(zip(multipliers, rows[79]['risk_labels']))
    assert by[2.0] == 1 and by[4.0] == 1 and by[8.0] == 0 and by[64.0] == 0
    inside = dict(zip(multipliers, rows[82]['risk_labels']))
    assert inside[2.0] is None and inside[4.0] is None    # breach minutes inside the window: neither class
    assert inside[8.0] == 0                               # below that multiplier the window stays a clean negative
    assert rows[79]['baseline_rt'] == pytest.approx(10.0)


def test_column_builder_matches_rows_and_skips_unrequested_splits():
    from sentinelops.ml.alibaba_training import build_arrays, build_rows
    config = load_config()
    rng = np.random.default_rng(3)
    frames = []
    for name in ('MS_a', 'MS_b', 'MS_c', 'MS_d'):
        rt = rng.lognormal(2.5, 0.3, 150)
        rt[100:104] *= 5
        frames.append(pd.DataFrame({'msname': name, 'minute': np.arange(150), 'http_rt_ms': rt,
                                    'http_mcr_sum': rng.lognormal(0, 0.1, 150)}))
    data = pd.concat(frames, ignore_index=True)
    rows, arrays = build_rows(data, config), build_arrays(data, config)
    assert len(rows) == len(arrays['minute'])
    assert np.allclose([r['features'][:5] for r in rows], arrays['features'])
    assert [r['service'] for r in rows] == [arrays['services'][i] for i in arrays['service']]
    assert [r['split'] for r in rows] == [arrays['service_split'][i] for i in arrays['service']]
    wanted = {split_of(n) for n in ('MS_a', 'MS_b', 'MS_c', 'MS_d')} - {'evaluation'}
    partial = build_arrays(data, config, splits=tuple(wanted)) if wanted else None
    assert partial is None or 'evaluation' not in set(partial['service_split'])


def test_serving_breach_is_the_frozen_slo_latency():
    import json
    from pathlib import Path
    policy = json.loads((Path(__file__).resolve().parents[1] / 'configs/slo_health_policy.json').read_text())
    assert load_config()['serving']['breach_latency_ms'] == policy['latency_ms']


def test_risk_labels_use_the_window_baseline_not_a_later_minute_baseline():
    from sentinelops.ml.alibaba_training import service_window_arrays
    config = load_config()
    multipliers = config['label']['breach_multipliers']
    # Only six baseline minutes exist before a ramp; a per-minute baseline would rise with the ramp.
    rt = [10.0] * 6 + [10.0] * 5 + [15.0, 25.0, 45.0, 85.0, 170.0, 170.0, 170.0] + [10.0] * 10
    arrays = service_window_arrays(series(rt), config)
    row = list(arrays['minute']).index(10)                  # window minutes 6..10, baseline 10 ms
    assert arrays['baseline_rt'][row] == pytest.approx(10.0)
    labels = dict(zip(multipliers, arrays['labels'][row]))
    onsets = dict(zip(multipliers, arrays['onsets'][row]))
    assert labels[8.0] == 1 and onsets[8.0] == 14             # 85 and 170 ms exceed 80 ms from minute 14
    assert labels[16.0] == 1 and onsets[16.0] == 15           # 170 ms twice exceeds 160 ms from minute 15
    assert labels[32.0] == 0                                  # never above 320 ms through horizon + tail
