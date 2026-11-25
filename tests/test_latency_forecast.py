"""Lane A benchmark targets use verified latency outcomes only."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sentinelops.ml.alibaba_local_evaluation import latency_forecast_labels, score_records
from sentinelops.ml.alibaba_training import load_config
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2
from sentinelops.telemetry.store import ArtifactStore
from test_health_source import capture
from test_local_labels import START, derive, fixture_oracle

POLICY = json.loads(Path('configs/slo_health_policy.json').read_text())
SERVICE = 'payment-service'


def inputs():
    frame = {'service': SERVICE, 'window_start': timestamp(START),
             'window_end': timestamp(START + timedelta(minutes=5)),
             'as_of': timestamp(START + timedelta(minutes=5, seconds=30))}
    return frame, derive(fixture_oracle())


def set_latency(oracle, offset, values):
    for index, value in enumerate(values):
        instant = START + timedelta(minutes=5, seconds=offset + index * 60)
        oracle['services'][SERVICE]['buckets'][str(int(instant.timestamp()))]['p95_latency_ms'] = value


@pytest.mark.parametrize('values,label', [([751., 800.], 1), ([750., 750.], 0)])
def test_a01_latency_threshold_is_strict_and_onset_uses_first_bucket(values, label):
    frame, oracle = inputs()
    set_latency(oracle, 240, values)
    original = deepcopy(oracle)
    result = latency_forecast_labels(frame, oracle, POLICY)
    assert result['risk_label'] == label
    assert result['risk_missing_reason'] is None
    assert result['slo_onset_at'] == (timestamp(START + timedelta(minutes=9)) if label else None)
    assert oracle == original


@pytest.mark.parametrize('offset,label,reason', [
    (0, None, 'incident-or-post-incident'), (300, 1, None), (360, 0, None),
])
def test_a02_forecast_onset_interval_is_open_left_and_closed_right(offset, label, reason):
    frame, oracle = inputs()
    set_latency(oracle, offset, [800., 900.])
    result = latency_forecast_labels(frame, oracle, POLICY)
    assert result['risk_label'] == label
    assert result['risk_missing_reason'] == reason


def test_a02_http_errors_do_not_create_latency_precursors():
    frame, oracle = inputs()
    evidence = oracle['services'][SERVICE]
    evidence['first_violation_at'] = timestamp(START + timedelta(minutes=9))
    evidence['confirmed_at'] = timestamp(START + timedelta(minutes=11))
    for item in evidence['buckets'].values():
        item.update(error_rate=.9, breach=True)
    result = latency_forecast_labels(frame, oracle, POLICY)
    assert result['risk_label'] == 0
    assert result['slo_onset_at'] is None


@pytest.mark.parametrize('missing', ['history', 'confirmation', 'traffic', 'tail', 'gap'])
def test_a03_missing_evidence_stays_unknown_with_reason(missing):
    frame, oracle = inputs()
    evidence = oracle['services'][SERVICE]
    if missing in ('confirmation', 'traffic'):
        set_latency(oracle, 240, [751., 800.])
        key = str(int((START + timedelta(minutes=10)).timestamp()))
        if missing == 'confirmation':
            del evidence['buckets'][key]
        else:
            evidence['buckets'][key].update(availability='UNKNOWN', direct_client_count=0)
    elif missing == 'gap':
        set_latency(oracle, 240, [751., 800.])
        del evidence['buckets'][str(int((START + timedelta(minutes=7)).timestamp()))]
    elif missing == 'history':
        evidence['buckets'][str(int(START.timestamp()))]['availability'] = 'UNKNOWN'
    else:
        oracle['ended_at'] = timestamp(START + timedelta(minutes=11))
    result = latency_forecast_labels(frame, oracle, POLICY)
    assert result['risk_label'] is None
    assert result['risk_missing_reason'] == (
        'incomplete-history-oracle' if missing == 'history' else 'insufficient-future-oracle')


def test_a03_late_cutoff_does_not_backdate_forecast():
    frame, oracle = inputs()
    frame['as_of'] = timestamp(START + timedelta(minutes=6))
    result = latency_forecast_labels(frame, oracle, POLICY)
    assert result['risk_label'] is None
    assert result['risk_missing_reason'] == 'forecast-anchor-minute-advanced'


def test_a03_already_breaching_history_is_not_healthy():
    frame, oracle = inputs()
    evidence = oracle['services'][SERVICE]
    evidence['buckets'][str(int((START + timedelta(minutes=4)).timestamp()))]['p95_latency_ms'] = 751.
    result = latency_forecast_labels(frame, oracle, POLICY)
    assert result['risk_label'] is None
    assert result['risk_missing_reason'] == 'history-not-healthy'


@pytest.mark.parametrize('field,value', [
    ('window_start', timestamp(START + timedelta(minutes=1))),
    ('window_end', timestamp(START + timedelta(minutes=5, seconds=1))),
    ('as_of', timestamp(START + timedelta(minutes=4, seconds=59))),
])
def test_invalid_feature_clocks_are_rejected(field, value):
    frame, oracle = inputs()
    frame[field] = value
    with pytest.raises(ValueError, match='five-minute UTC window'):
        latency_forecast_labels(frame, oracle, POLICY)


def test_a03_future_suffix_cannot_change_features_or_prediction(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    pytest.importorskip("xgboost")

    store = ArtifactStore(tmp_path)
    serving = ArtifactStore(tmp_path / 'capture' / 'serving')
    config = load_config()
    end = START + timedelta(minutes=65)
    row = {'service': SERVICE, 'window_end': timestamp(end),
           'prediction_cutoff': timestamp(end + timedelta(seconds=30))}
    history = [capture(SERVICE, START + timedelta(minutes=minute), errors=0, profile=SOURCE_PROFILE_V2)
               for minute in range(1, 66)]
    suffix = [capture(SERVICE, START + timedelta(minutes=minute), errors=120, profile=SOURCE_PROFILE_V2)
              for minute in range(66, 71)]
    for raw in suffix:
        raw['queries']['duration_sum']['response']['data']['result'][0]['value'][1] = '1200000'
    detector = SimpleNamespace(predict=lambda values: {
        name: {'alert': [False] * len(values)} for name in ('isolation_forest', 'median_mad')})
    def predict_proba(values):
        scores = 1 / (1 + np.exp(-np.asarray(values).sum(axis=1)))
        return np.column_stack((1 - scores, scores))

    risk = SimpleNamespace(predict_proba=predict_proba)
    scoring = {'error_mad_min_scale': .01, 'error_mad_multiplier': 3., 'risk_relative_slope_threshold': .1}

    def score(captures):
        normalized = process_captures(captures, serving, default_profile=SOURCE_PROFILE_V2)
        source_ref = serving.write('manifests', {'normalization_manifest_ref': normalized['manifest_ref']})
        run = {'parameters': {'fault_mode': 'latency'}, 'source_manifest_ref': source_ref}
        (tmp_path / 'capture' / 'run.json').write_text(json.dumps(run))
        source_result_ref = store.write('manifests', {'run_directory': 'capture', 'run_sha256': stable_id(run)})
        records = score_records(store, [{'source_result_ref': source_result_ref, 'rows': [row]}],
                                detector, risk, config, scoring, {}, POLICY, .8)
        return records[0]

    complete = score(history + suffix)
    truncated = score(history)
    assert complete['relative_features'] is not None
    assert 0 <= complete['xgboost_score'] <= 1
    assert complete == truncated
    late = deepcopy(history)
    late[-1]['collected_at'] = timestamp(end + timedelta(seconds=31))
    unavailable = score(late)
    assert unavailable['relative_features'] is None
    assert unavailable['xgboost_score'] is None
