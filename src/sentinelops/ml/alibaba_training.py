"""ADR-016: causal service-relative windows and breach labels from Alibaba minutes.

The frozen config (`configs/alibaba_relative_features.json`) fixes features, the
breach rule, horizon and the microservice split before any fit. Splits are by
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


def load_config(path=CONFIG):
    config = json.loads(Path(path).read_text())
    if config.get('feature_set_version') != 'http-relative-5m/v1' or config.get('frozen_before_fit') is not True:
        raise ValueError('unsupported or unfrozen ADR-016 feature config')
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
    breach = (ratio > 2.0).astype(float).where(ratio.notna())
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


def service_windows_fast(series, config):
    """Vectorised equivalent of ``service_windows`` (same frozen semantics; tested equal)."""
    import numpy as np
    import pandas as pd
    from numpy.lib.stride_tricks import sliding_window_view
    w = config['window_minutes']
    base = config['baseline']
    full = series.reindex(range(series.index.min(), series.index.max() + 1))
    rt_s, rate_s = full['rt'], full['rate']
    roll = lambda s: s.rolling(base['max_minutes'], min_periods=base['min_minutes']).median().shift(w)
    rt_base, rate_base = roll(rt_s).to_numpy(float), roll(rate_s).to_numpy(float)
    minute_base = rt_s.rolling(base['max_minutes'], min_periods=base['min_minutes']).median().shift(1)
    ratio = rt_s / minute_base
    breach_s = (ratio > 2.0).astype(float).where(ratio.notna())
    consecutive = config['label']['breach_onset_requires_consecutive_minutes']
    confirmed = breach_s.rolling(consecutive).sum() == consecutive
    onset = confirmed & ~confirmed.shift(1, fill_value=False)
    index = full.index.to_numpy()
    onset_minute = np.where(onset.to_numpy(), index - (consecutive - 1), np.nan).astype(float)
    horizon = config['label']['horizon_seconds'] // 60
    tail = config['label']['confirmation_tail_minutes']
    n = len(index)
    if n < w:
        return []
    rt, rate, breach = rt_s.to_numpy(float), rate_s.to_numpy(float), breach_s.to_numpy(float)
    pad = np.full(horizon + tail + consecutive, np.nan)
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
                                 slope(rel_rate), np.log(np.nanmax(rel_rt, 1))]) if len(pos) else np.empty((0, 5))
    ok &= np.isfinite(feats).all(1)
    breach_p, onset_p = np.concatenate([breach, pad]), np.concatenate([onset_minute, pad])
    fut = sliding_window_view(breach_p[1:], horizon + tail)[pos]
    win = sliding_window_view(breach, w)
    ons = sliding_window_view(onset_p[1:], horizon + consecutive - 1)[pos]
    rows = []
    for k in np.nonzero(ok)[0]:
        t = int(index[pos[k]])
        in_window, future = win[k], fut[k]
        cand = ons[k][~np.isnan(ons[k])]
        cand = cand[(cand > t) & (cand <= t + horizon)]
        window_seen = np.isfinite(in_window).all()
        observed = np.isfinite(future).all() and window_seen
        healthy = bool(observed and (in_window == 0).all() and (future == 0).all())
        risk = 1 if (window_seen and (in_window == 0).all() and len(cand)) else (0 if healthy else None)
        rows.append({'minute': t, 'features': [float(v) for v in feats[k]], 'healthy': healthy,
                     'risk_label': risk, 'onset_minute': int(cand[0]) if len(cand) else None})
    return rows


def build_rows(data, config):
    """All services with enough minutes; split assigned by identity before rows exist."""
    rows = []
    for msname, group in data.groupby('msname'):
        if group.minute.nunique() < config['splits']['min_minutes_per_service']:
            continue
        series = group.set_index('minute')[['http_rt_ms', 'http_mcr_sum']].rename(
            columns={'http_rt_ms': 'rt', 'http_mcr_sum': 'rate'}).sort_index()
        split = split_of(msname)
        for row in service_windows_fast(series, config):
            rows.append({'service': msname, 'split': split, **row})
    return rows
