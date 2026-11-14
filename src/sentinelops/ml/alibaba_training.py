"""ADR-016/ADR-019: causal service-relative windows and breach labels from Alibaba minutes.

The frozen config (`configs/alibaba_relative_features.json`) fixes features, the
breach rule and its multipliers, horizon and the microservice split before any fit. Splits are by
microservice identity, so evaluation services are never seen in fitting. All
transforms use only minutes at or before each window end, except labels, which
read the future and are never model inputs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[3] / 'configs/alibaba_relative_features.json'
MINUTE_MS = 60_000
# Computed from the service's own minutes. The last config feature is appended by the caller:
# log(baseline latency / breach latency), -log(multiplier) in training and log(baseline / SLO) when serving.
BASE_FEATURES = ('rt_log_level_rel', 'rt_slope_rel_per_min', 'rate_log_level_rel', 'rate_slope_rel_per_min',
                 'rt_log_peak_rel')
BREACH_FEATURE = 'rt_log_baseline_to_breach'


def load_config(path=CONFIG):
    config = json.loads(Path(path).read_text())
    if config.get('feature_set_version') != 'http-relative-5m/v2' or config.get('frozen_before_fit') is not True:
        raise ValueError('unsupported or unfrozen ADR-019 feature config')
    multipliers = config['label']['breach_multipliers']
    if (not multipliers or multipliers != sorted(set(multipliers)) or multipliers[0] <= 1
            or config['features'][:len(BASE_FEATURES)] != list(BASE_FEATURES)
            or config['features'][len(BASE_FEATURES):] != [BREACH_FEATURE]
            or any(name not in BASE_FEATURES for name in config['detector']['features'])
            or any(rule['multiplier'] not in multipliers for rule in config['risk']['thresholds'].values())
            or not config['serving']['breach_latency_ms'] > 0):
        raise ValueError('ADR-019 feature config is inconsistent')
    return config


def split_of(msname):
    bucket = int(hashlib.sha256(msname.encode()).hexdigest(), 16) % 10
    return 'train' if bucket <= 5 else 'calibration' if bucket <= 7 else 'evaluation'


def load_minutes(directory):
    """Concatenate verified aggregates into one (minute × service) table."""
    import pandas as pd
    from sentinelops.public_data.alibaba import AGGREGATE_VERSION, file_sha256
    frames, provenance = [], []
    for meta in sorted(Path(directory).glob('MCRRTUpdate_*.json'), key=lambda p: int(p.stem.split('_')[1])):
        record = json.loads(meta.read_text())
        parquet = meta.with_suffix('.parquet')
        if record['aggregate_version'] != AGGREGATE_VERSION or file_sha256(parquet) != record['parquet_sha256']:
            raise ValueError('aggregate differs from its recorded provenance: ' + meta.name)
        frames.append(pd.read_parquet(parquet))
        provenance.append({k: record[k] for k in ('archive', 'archive_sha256', 'parquet_sha256')})
    if not frames:
        raise ValueError('no verified Alibaba aggregates')
    data = pd.concat(frames, ignore_index=True)
    data = data.groupby(['timestamp', 'msname'], as_index=False).agg(
        http_mcr_sum=('http_mcr_sum', 'sum'), http_rt_ms=('http_rt_ms', 'mean'))
    data['minute'] = (data.timestamp // MINUTE_MS).astype(int)
    return data, provenance


def _ols_slope(values):
    import numpy as np
    y = np.asarray(values, dtype=float)
    x = np.arange(len(y), dtype=float)
    ok = np.isfinite(y)
    if ok.sum() < 4:
        return np.nan
    x, y = x[ok], y[ok]
    x = x - x.mean()
    return float((x * (y - y.mean())).sum() / (x * x).sum())


def service_windows(series, config):
    """Rows for one service: series is a minute-indexed frame with rt and rate."""
    import numpy as np
    import pandas as pd
    w = config['window_minutes']
    base = config['baseline']
    full = series.reindex(range(series.index.min(), series.index.max() + 1))
    rt, rate = full['rt'], full['rate']
    # Baseline for a window ending at t: minutes (t-w-max, t-w], i.e. strictly before the window.
    roll = lambda s: s.rolling(base['max_minutes'], min_periods=base['min_minutes']).median().shift(w)
    rt_base, rate_base = roll(rt), roll(rate)
    # Breach minute uses a baseline strictly before that minute (label side only).
    rt_minute_base = rt.rolling(base['max_minutes'], min_periods=base['min_minutes']).median().shift(1)
    ratio = rt / rt_minute_base
    breach = (ratio > config['label']['breach_multipliers'][0]).astype(float).where(ratio.notna())
    consecutive = config['label']['breach_onset_requires_consecutive_minutes']
    confirmed = breach.rolling(consecutive).sum() == consecutive
    onset = confirmed & ~confirmed.shift(1, fill_value=False)
    onset_minute = pd.Series(np.where(onset, full.index - (consecutive - 1), np.nan), index=full.index)
    horizon = config['label']['horizon_seconds'] // 60
    tail = config['label']['confirmation_tail_minutes']
    rows = []
    index = list(full.index)
    for pos in range(w - 1, len(index)):
        t = index[pos]
        window_rt = rt.iloc[pos - w + 1:pos + 1]
        window_rate = rate.iloc[pos - w + 1:pos + 1]
        b_rt, b_rate = rt_base.iloc[pos], rate_base.iloc[pos]
        if window_rt.notna().sum() < 4 or not (b_rt > 0) or not (b_rate > 0) or not np.isfinite(b_rt):
            continue
        rel_rt, rel_rate = window_rt / b_rt, window_rate / b_rate
        if (rel_rt <= 0).any() or (rel_rate <= 0).any():
            continue
        features = [float(np.log(rel_rt.mean())), _ols_slope(rel_rt.values),
                    float(np.log(rel_rate.mean())), _ols_slope(rel_rate.values),
                    float(np.log(rel_rt.max()))]
        if not np.all(np.isfinite(features)):
            continue
        future = breach.iloc[pos + 1:pos + 1 + horizon + tail]
        in_window = breach.iloc[pos - w + 1:pos + 1]
        future_onsets = onset_minute.iloc[pos + 1:pos + 1 + horizon + consecutive - 1].dropna()
        future_onsets = future_onsets[(future_onsets > t) & (future_onsets <= t + horizon)]
        observed = len(future) == horizon + tail and future.notna().all() and in_window.notna().all()
        clean_window = observed and (in_window == 0).all()
        healthy = clean_window and (future == 0).all()
        risk = 1 if (in_window.notna().all() and (in_window == 0).all() and len(future_onsets)) else (0 if healthy else None)
        rows.append({'minute': int(t), 'features': features, 'healthy': bool(healthy), 'risk_label': risk,
                     'onset_minute': int(future_onsets.iloc[0]) if len(future_onsets) else None})
    return rows


def service_window_arrays(series, config):
    """Vectorised windows for one service: base features, window baseline and risk labels per multiplier.

    ``healthy`` and ``onset_minute`` keep the ADR-016 detector semantics (first multiplier, per-minute
    trailing baseline). Risk labels compare future minutes with K times the window's own baseline: 1 when
    the window stays at or below that level and two consecutive minutes exceed it with the first in
    (t, t+300s]; 0 when the window and horizon + tail are fully observed at or below it; otherwise -1.
    """
    import numpy as np
    import pandas as pd
    from numpy.lib.stride_tricks import sliding_window_view
    w = config['window_minutes']
    base = config['baseline']
    multipliers = config['label']['breach_multipliers']
    empty = {'minute': np.empty(0, int), 'features': np.empty((0, len(BASE_FEATURES))), 'baseline_rt': np.empty(0),
             'healthy': np.empty(0, bool), 'labels': np.empty((0, len(multipliers)), np.int8),
             'onsets': np.empty((0, len(multipliers))), 'onset_minute': np.empty(0)}
    if not len(series):
        return empty
    full = series.reindex(range(series.index.min(), series.index.max() + 1))
    rt_s, rate_s = full['rt'], full['rate']
    roll = lambda s: s.rolling(base['max_minutes'], min_periods=base['min_minutes']).median().shift(w)
    rt_base, rate_base = roll(rt_s).to_numpy(float), roll(rate_s).to_numpy(float)
    minute_base = rt_s.rolling(base['max_minutes'], min_periods=base['min_minutes']).median().shift(1)
    ratio = rt_s / minute_base
    consecutive = config['label']['breach_onset_requires_consecutive_minutes']
    index = full.index.to_numpy()
    horizon = config['label']['horizon_seconds'] // 60
    tail = config['label']['confirmation_tail_minutes']
    n = len(index)
    if n < w:
        return empty
    rt, rate = rt_s.to_numpy(float), rate_s.to_numpy(float)
    rt_win, rate_win = sliding_window_view(rt, w), sliding_window_view(rate, w)
    pos = np.arange(w - 1, n)
    b_rt, b_rate = rt_base[pos], rate_base[pos]
    import warnings
    with np.errstate(invalid='ignore', divide='ignore'), warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)  # all-NaN windows are excluded below
        rel_rt, rel_rate = rt_win / b_rt[:, None], rate_win / b_rate[:, None]
        ok = ((np.isfinite(rt_win).sum(1) >= 4) & (b_rt > 0) & (b_rate > 0) & np.isfinite(b_rt)
              & ~(rel_rt <= 0).any(1) & ~(rel_rate <= 0).any(1))

        def slope(y):
            m = np.isfinite(y)
            cnt = m.sum(1)
            x = np.broadcast_to(np.arange(w, dtype=float), y.shape)
            xm = np.where(m, x, 0).sum(1) / np.maximum(cnt, 1)
            ym = np.where(m, y, 0).sum(1) / np.maximum(cnt, 1)
            dx = np.where(m, x - xm[:, None], 0)
            val = (dx * np.where(m, y - ym[:, None], 0)).sum(1) / (dx * dx).sum(1)
            return np.where(cnt >= 4, val, np.nan)

        feats = np.column_stack([np.log(np.nanmean(rel_rt, 1)), slope(rel_rt), np.log(np.nanmean(rel_rate, 1)),
                                 slope(rel_rate), np.log(np.nanmax(rel_rt, 1))])
    ok &= np.isfinite(feats).all(1)
    t = index[pos].astype(float)
    pad = np.full(horizon + tail + consecutive, np.nan)
    # Detector semantics (ADR-016, first multiplier): per-minute trailing baseline, healthy windows and onsets.
    breach_s = (ratio > multipliers[0]).astype(float).where(ratio.notna())
    confirmed = breach_s.rolling(consecutive).sum() == consecutive
    onset = confirmed & ~confirmed.shift(1, fill_value=False)
    onset_minute = np.where(onset.to_numpy(), index - (consecutive - 1), np.nan).astype(float)
    breach = breach_s.to_numpy(float)
    fut = sliding_window_view(np.concatenate([breach, pad])[1:], horizon + tail)[pos]
    win = sliding_window_view(breach, w)
    ons = sliding_window_view(np.concatenate([onset_minute, pad])[1:], horizon + consecutive - 1)[pos]
    with np.errstate(invalid='ignore'):
        candidate = np.isfinite(ons) & (ons > t[:, None]) & (ons <= t[:, None] + horizon)
        healthy = (np.isfinite(win).all(1) & (win == 0).all(1) & np.isfinite(fut).all(1) & (fut == 0).all(1))
    first = np.where(candidate, ons, np.inf).min(1)
    onset_first = np.where(candidate.any(1), first, np.nan)
    # Risk semantics (ADR-019): the breach is K times the window's own baseline, the same level the
    # breach-relative feature encodes, so training and serving share one target definition.
    future_rt = sliding_window_view(np.concatenate([rt, pad])[1:], horizon + tail)[pos]
    labels = np.full((len(pos), len(multipliers)), -1, np.int8)
    onsets = np.full((len(pos), len(multipliers)), np.nan)
    with np.errstate(invalid='ignore'):
        for column, multiplier in enumerate(multipliers):
            level = (multiplier * b_rt)[:, None]
            clean = np.isfinite(rt_win).all(1) & (rt_win <= level).all(1)
            above = future_rt > level
            starts = above[:, :horizon] & above[:, 1:horizon + 1]
            negative = clean & np.isfinite(future_rt).all(1) & ~above.any(1)
            positive = clean & starts.any(1)
            labels[negative, column] = 0
            labels[positive, column] = 1
            onsets[positive, column] = t[positive] + 1 + starts[positive].argmax(1)
    return {'minute': index[pos][ok].astype(int), 'features': feats[ok], 'baseline_rt': b_rt[ok],
            'healthy': healthy[ok], 'labels': labels[ok], 'onsets': onsets[ok], 'onset_minute': onset_first[ok]}


def serving_breach_feature(baseline_rt, config):
    """log(baseline latency / breach latency) for a breach at the serving SLO (mean latency, milliseconds)."""
    import math
    return math.log(baseline_rt / config['serving']['breach_latency_ms'])


def service_windows_fast(series, config):
    """Row form of ``service_window_arrays`` with every config feature at the serving breach level.

    The base features and the first-multiplier labels are tested equal to ``service_windows``. The last
    feature places the serving SLO relative to this window's baseline; training rebuilds it per multiplier.
    """
    arrays = service_window_arrays(series, config)
    rows = []
    for i, minute in enumerate(arrays['minute']):
        onset = arrays['onset_minute'][i]
        values = [float(v) for v in arrays['features'][i]] + [serving_breach_feature(float(arrays['baseline_rt'][i]), config)]
        rows.append({'minute': int(minute), 'features': values,
                     'healthy': bool(arrays['healthy'][i]),
                     'onset_minute': None if onset != onset else int(onset),
                     'baseline_rt': float(arrays['baseline_rt'][i]),
                     'risk_labels': [None if v < 0 else int(v) for v in arrays['labels'][i]]})
    return rows


def _service_series(data, config):
    for msname, group in data.groupby('msname'):
        if group.minute.nunique() < config['splits']['min_minutes_per_service']:
            continue
        yield msname, group.set_index('minute')[['http_rt_ms', 'http_mcr_sum']].rename(
            columns={'http_rt_ms': 'rt', 'http_mcr_sum': 'rate'}).sort_index()


def build_rows(data, config):
    """All services with enough minutes; split assigned by identity before rows exist."""
    rows = []
    for msname, series in _service_series(data, config):
        split = split_of(msname)
        for row in service_windows_fast(series, config):
            rows.append({'service': msname, 'split': split, **row})
    return rows


def build_arrays(data, config, *, splits=('train', 'calibration', 'evaluation')):
    """Column form of ``build_rows`` for fitting; services outside ``splits`` are never read into windows."""
    import numpy as np
    parts, services, split_names = [], [], []
    for msname, series in _service_series(data, config):
        split = split_of(msname)
        if split not in splits:
            continue
        arrays = service_window_arrays(series, config)
        if len(arrays['minute']):
            arrays['service'] = np.full(len(arrays['minute']), len(services))
            parts.append(arrays); services.append(msname); split_names.append(split)
    if not parts:
        raise ValueError('no Alibaba service has enough minutes')
    result = {key: np.concatenate([part[key] for part in parts]) for key in parts[0]}
    result['services'] = services
    result['service_split'] = np.array(split_names)
    return result
