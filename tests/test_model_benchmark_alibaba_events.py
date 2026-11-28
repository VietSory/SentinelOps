from datetime import datetime, timedelta, timezone
import hashlib
import json

import pyarrow as arrow
import pyarrow.parquet as parquet
import pytest

from scripts.project_alibaba_detector_events import run
from sentinelops.ml.alibaba_training import split_of
from sentinelops.ml.model_benchmark_alibaba_events import (
    ALIBABA_EVENT_RULE, ALIBABA_EVENT_RULE_ID, project_alibaba_proxy_events, proxy_minute_labels,
)
from sentinelops.ml.model_benchmark_detector_combo import COMBO_MODEL, append_combo_predictions
from sentinelops.ml.model_benchmark_detector_evaluation import (
    KEY_FIELDS, MODELS, _audit_events, evaluate_detectors,
)
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.ml.model_benchmark_detector_fit import DetectorMinute, project_detector_run


ORIGIN = datetime(2026, 1, 1, tzinfo=timezone.utc)
EVALUATION_SERVICES = tuple(service for service in (f'service-{index}' for index in range(100))
                            if split_of(service) == 'evaluation')[:3]
SERVICE = EVALUATION_SERVICES[0]


def _at(minute, seconds=0):
    return timestamp(ORIGIN + timedelta(minutes=minute, seconds=seconds))


def _windows(labels, *, service=SERVICE, start=324, stop=352, omitted=()):
    return [{'dataset': 'alibaba-proxy', 'group_id': service, 'run_id': service, 'service': service,
             'bucket_end': _at(minute), 'relative_minute': minute, 'eligible': minute >= 324,
             'value': None if labels.get(minute) is None and minute in labels else 30.0 if labels.get(minute) == 1 else 10.0,
             'y': labels.get(minute, 0)}
            for minute in range(start, stop + 1) if minute not in omitted]


def _key(row):
    return tuple(row[field] for field in KEY_FIELDS)


@pytest.mark.parametrize(('labels', 'omitted', 'expected'), [
    ({330: 1}, (), [(330, 330, None)]),
    ({330: 1, 331: 1, 336: 1}, (), [(330, 336, None)]),
    ({330: 1, 336: 1}, (), [(330, 330, None), (336, 336, None)]),
    ({330: 1, 332: None, 335: 1}, (), [(330, 330, None), (335, 335, 'unknown-pre-onset-history')]),
    ({330: 1, 335: 1}, (332,), [(330, 330, None), (335, 335, 'unknown-pre-onset-history')]),
    ({328: 1}, (), [(328, 328, 'insufficient-pre-onset-history')]),
    ({325: None, 329: 1}, (), [(329, 329, 'unknown-pre-onset-history')]),
    ({347: 1}, (), [(347, 347, None)]),
    ({348: 1}, (), [(348, 348, 'insufficient-follow-up')]),
])
def test_event_segmentation_eligibility_and_identity(labels, omitted, expected):
    windows = _windows(labels, omitted=omitted)
    events, healthy = project_alibaba_proxy_events(windows)
    assert events == [{
        'event_id': stable_id({'rule_id': ALIBABA_EVENT_RULE_ID, 'service': SERVICE, 'onset_at': _at(onset)}),
        'dataset': 'alibaba-proxy', 'group_id': SERVICE, 'run_id': SERVICE, 'service': SERVICE,
        'kind': 'alibaba-proxy', 'onset_at': _at(onset), 'confirmed_at': _at(onset), 'end_at': _at(end),
        'length_minutes': end - onset + 1, 'eligible': reason is None, 'reason': reason,
        'positive_minutes': sum(labels.get(minute) == 1 for minute in range(onset, end + 1)),
        'censor_until': _at(end + 5) if reason is not None else None,
    } for onset, end, reason in expected]
    assert project_alibaba_proxy_events(reversed(windows)) == (events, healthy)
    assert ALIBABA_EVENT_RULE_ID == stable_id(ALIBABA_EVENT_RULE)


def test_declared_scope_covers_missing_edges_and_healthy_minutes_exclude_ineligible_events():
    windows = _windows({330: 1, 331: 1, 338: None, 352: 1})
    events, healthy = project_alibaba_proxy_events(windows)
    assert not events[-1]['eligible']
    assert healthy == {_key(row) for row in windows if row['relative_minute'] in {324, *range(337, 347)} - {338}}

    sparse = _windows({347: 1}, start=342, stop=347)
    assert project_alibaba_proxy_events(sparse)[0][0]['eligible']
    sparse = _windows({329: 1}, start=325, stop=330)
    assert project_alibaba_proxy_events(sparse)[0][0]['reason'] == 'insufficient-pre-onset-history'


def test_audit_and_evaluate_keep_late_alert_unmatched_and_unscored_event_as_miss():
    first, late, unscored = EVALUATION_SERVICES
    windows = [row for service in (unscored, late, first) for row in _windows({330: 1}, service=service)]
    for row in windows:
        row['available_at'] = row['bucket_end']
        row['decision_at'] = _at(335, 1) if row['service'] == late and row['bucket_end'] == _at(335) else row['bucket_end']
    events, healthy = project_alibaba_proxy_events(windows)
    assert [event['service'] for event in events] == sorted((first, late, unscored))
    for row in windows:
        row['healthy'] = _key(row) in healthy
    predictions = [{**dict(zip(KEY_FIELDS, _key(row), strict=True)), 'model_id': model,
                    'score': 2.0 if (row['service'], row['bucket_end']) in {(first, _at(332)), (late, _at(335))} else 0.0}
                   for row in windows if row['service'] != unscored
                   for model in MODELS]
    operating = {'status': 'ready', 'threshold': 1.0, 'thresholds': {'median_mad': 1.0, 'adsketch': 1.0}}
    predictions = append_combo_predictions(predictions, operating)

    audited = _audit_events({_key(row): row for row in windows}, events, 'alibaba-proxy')
    assert all(event['in_scope'] and event['deadline_at'] == _at(335) for event in audited)
    result = evaluate_detectors(
        windows, events, predictions, clock='arrival-aware', thresholds=dict.fromkeys((*MODELS, COMBO_MODEL), 1.0),
        component_thresholds=operating['thresholds'],
        policy_identity='fixture', persistence={'step_seconds': 60, 'entry_windows': 1, 'exit_windows': 1},
        event_kind='alibaba-proxy',
    )
    model = result['models']['median_mad']
    assert (model['tp'], model['fp'], model['miss'], model['f1']) == (1, 1, 2, .4)
    assert model['false_incidents_per_healthy_service_hour'] == 0.0
    matches = [row for row in result['matches'] if row['model_id'] == 'median_mad']
    assert {(row['service'], row['status'], row['emitted_at'], row['reason']) for row in matches} == {
        (first, 'matched', _at(332), None),
        (late, 'unmatched', _at(335, 1), 'no-unmatched-eligible-event-in-window'),
        (late, 'miss', None, 'no-matching-alert'),
        (unscored, 'miss', None, 'no-available-score-in-window'),
    }
    combo = result['models'][COMBO_MODEL]
    assert (combo['tp'], combo['fp'], combo['miss']) == (1, 1, 2)
    assert combo['false_incidents_per_healthy_service_hour'] == 0.0


def _descriptor(root, windows):
    path = root / 'windows.parquet'
    parquet.write_table(arrow.Table.from_pylist(windows), path)
    ref = {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    descriptor = root / 'score.json'
    descriptor.write_text(json.dumps({'schema_version': 'detector-score-input/development-v1',
                                      'dataset': 'alibaba-proxy', 'clock': 'replay', 'fit_sha256': '0' * 64,
                                      'windows': ref, 'events': {}, 'admission': {}}))
    return descriptor, ref


def test_projection_writes_pinned_events_and_receipt(tmp_path):
    windows = _windows({330: 1, 331: 1, 352: 1}, start=259)
    descriptor, ref = _descriptor(tmp_path, windows)
    output = tmp_path / 'output'
    receipt = run(tmp_path, descriptor=descriptor, output=output)
    assert receipt == json.loads((output / 'receipt.json').read_bytes())
    assert receipt == {
        'schema_version': 'alibaba-detector-event-projection/v1', 'rule_id': ALIBABA_EVENT_RULE_ID,
        'windows': ref, 'events': {'path': 'events.parquet',
                                   'sha256': hashlib.sha256((output / 'events.parquet').read_bytes()).hexdigest()},
        'projected_windows': {'path': 'windows.parquet',
                              'sha256': hashlib.sha256((output / 'windows.parquet').read_bytes()).hexdigest()},
        'eligible_events': 1, 'ineligible_events': 1, 'ineligible_by_reason': {'insufficient-follow-up': 1},
        'services_with_events': 1, 'events_by_service': {SERVICE: 2},
        'event_lengths': {'one_minute': 1, 'two_or_more_minutes': 1}, 'healthy_windows': 11,
    }
    assert parquet.read_table(output / 'events.parquet').to_pylist() == project_alibaba_proxy_events(windows)[0]
    projected = parquet.read_table(output / 'windows.parquet').to_pylist()
    assert all(not row['eligible'] and not row['healthy'] for row in projected if row['relative_minute'] < 324)
    assert all(row['y'] is None for row in projected[:5])
    assert sum(row['healthy'] for row in projected) == receipt['healthy_windows']
    assert run(tmp_path, descriptor=descriptor, output=tmp_path / 'repeat') == receipt


def test_projection_accepts_explicit_origin_and_preserves_empty_event_schema(tmp_path):
    windows = _windows({})
    for row in windows:
        row.pop('relative_minute')
    descriptor, _ = _descriptor(tmp_path, windows)
    output = tmp_path / 'output'
    with pytest.raises(ValueError, match='relative_minute or --minute-zero'):
        run(tmp_path, descriptor=descriptor, output=output)
    receipt = run(tmp_path, descriptor=descriptor, output=output, minute_zero=_at(0))
    assert receipt['minute_zero'] == _at(0)
    events = parquet.read_table(output / 'events.parquet')
    assert events.num_rows == 0 and events.schema.field('event_id').type == arrow.string()


@pytest.mark.parametrize(('windows', 'match'), [
    (_windows({}, service='not-evaluation'), 'requires evaluation services'),
    (_windows({}, start=258), 'relative minute must be in'),
    (_windows({}, stop=353), 'relative minute must be in'),
    ([*_windows({}), {**_windows({})[0], 'relative_minute': 352, 'bucket_end': _at(319)}], 'one relative-minute origin'),
])
def test_projection_rejects_out_of_scope_windows(tmp_path, windows, match):
    descriptor, _ = _descriptor(tmp_path, windows)
    output = tmp_path / 'output'
    with pytest.raises(ValueError, match=match):
        run(tmp_path, descriptor=descriptor, output=output)
    assert not output.exists()


def test_projection_rejects_unpinned_window_bytes(tmp_path):
    descriptor, _ = _descriptor(tmp_path, _windows({}))
    (tmp_path / 'windows.parquet').write_bytes(b'changed')
    with pytest.raises(ValueError, match='SHA-256 differs from descriptor'):
        run(tmp_path, descriptor=descriptor, output=tmp_path / 'output')


def _minutes(values, *, service=SERVICE, start=259):
    return [DetectorMinute(service, int((ORIGIN + timedelta(minutes=start + index)).timestamp()), value)
            for index, value in enumerate(values)]


def test_oracle_matches_fit_per_minute_and_uses_strict_causal_threshold():
    minutes = _minutes([10.0] * 5 + [20.0, 20.01, None] + [10.0] * 5 + [30.0])
    labelled = proxy_minute_labels(reversed(minutes))
    fitted = project_detector_run(minutes, run_id='fixture', split='calibration')
    assert [row['y'] for row in labelled] == [row.label for row in fitted]
    assert [row['y'] for row in labelled] == [None] * 5 + [0, 1, None] + [None] * 5 + [1]
    assert proxy_minute_labels(minutes[:7]) == labelled[:7]
    assert all(set(row) == {'service', 'timestamp', 'y'} for row in labelled)
    assert [row['timestamp'] for row in labelled] == [minute.timestamp for minute in minutes]


def test_oracle_uses_last_sixty_minutes_and_resets_for_gap_or_zero_baseline():
    minutes = _minutes([1.0] * 60 + [10.0] * 60 + [20.0, 20.01])
    labelled = proxy_minute_labels(minutes)
    assert [row['y'] for row in labelled[-2:]] == [0, 1]
    gapped = _minutes([10.0] * 7)
    gapped.pop(5)
    assert proxy_minute_labels(gapped)[-1]['y'] is None
    assert proxy_minute_labels(_minutes([0.0] * 5 + [10.0]))[-1]['y'] is None
    multiple = [*_minutes([10.0] * 5 + [30.0]), *_minutes([100.0] * 5 + [150.0], service=EVALUATION_SERVICES[1])]
    result = proxy_minute_labels(reversed(multiple))
    assert result == sorted(result, key=lambda row: (row['service'], row['timestamp']))
    assert {row['service']: row['y'] for row in result if row['timestamp'] == multiple[5].timestamp} == {
        SERVICE: 1, EVALUATION_SERVICES[1]: 0,
    }


@pytest.mark.parametrize('minutes', [
    [*_minutes([10.0]), *_minutes([20.0])],
    [DetectorMinute(SERVICE, 61, 10.0)],
    _minutes([-1.0]), _minutes([float('nan')]), _minutes([True]),
])
def test_oracle_rejects_invalid_raw_minutes(minutes):
    with pytest.raises(ValueError):
        proxy_minute_labels(minutes)


def test_warmup_events_merge_across_scoring_boundary_and_supply_pre_onset_history():
    windows = _windows({322: 1, 326: 1, 340: 1}, start=259)
    events, healthy = project_alibaba_proxy_events(windows)
    assert [(event['onset_at'], event['end_at'], event['reason'], event['positive_minutes']) for event in events] == [
        (_at(322), _at(326), 'onset-before-scoring-range', 2), (_at(340), _at(340), None, 1),
    ]
    assert events[0]['length_minutes'] == 5 and events[0]['censor_until'] == _at(331)
    assert healthy == {_key(row) for row in windows if row['relative_minute'] in {*range(332, 335), *range(346, 353)}}
    for onset in (324, 328):
        assert project_alibaba_proxy_events(_windows({onset: 1}, start=259))[0][0]['eligible']


def test_ineligible_reason_precedence_and_history_are_per_service():
    assert project_alibaba_proxy_events(_windows({259: 1}, start=259))[0][0]['reason'] == 'onset-before-scoring-range'
    windows = _windows({348: 1, 346: None}, start=347)
    assert project_alibaba_proxy_events(windows)[0][0]['reason'] == 'insufficient-pre-onset-history'
    windows = _windows({348: 1, 346: None})
    assert project_alibaba_proxy_events(windows)[0][0]['reason'] == 'unknown-pre-onset-history'
    windows = [*_windows({}, start=259), *_windows({329: 1}, service=EVALUATION_SERVICES[1], start=325)]
    assert project_alibaba_proxy_events(windows)[0][0]['reason'] == 'insufficient-pre-onset-history'


def test_healthy_keys_normalize_utc_and_ignore_warmup_and_post_scoring_events():
    windows = _windows({353: 1}, start=259, stop=353)
    for row in windows:
        row['bucket_end'] = row['bucket_end'].replace('Z', '+00:00')
    events, healthy = project_alibaba_proxy_events(windows)
    assert events == []
    assert healthy == {('alibaba-proxy', SERVICE, SERVICE, SERVICE, _at(minute)) for minute in range(324, 353)}
    without_minutes = _windows({324: 1}, start=259)
    for row in without_minutes:
        row.pop('relative_minute')
    with pytest.raises(ValueError, match='relative_minute required on every'):
        project_alibaba_proxy_events(without_minutes)


@pytest.mark.parametrize(('changes', 'match'), [
    ({'y': True}, 'label must be'), ({'y': 2}, 'label must be'),
    ({'bucket_end': _at(324, 1)}, 'close on a UTC minute'),
    ({'eligible': 1}, 'eligibility must be boolean'),
])
def test_projector_rejects_invalid_windows(changes, match):
    windows = _windows({})
    windows[0].update(changes)
    with pytest.raises(ValueError, match=match):
        project_alibaba_proxy_events(windows)


def test_projector_rejects_duplicate_canonical_buckets_and_eligible_warmup():
    windows = _windows({})
    duplicate = {**windows[0], 'bucket_end': windows[0]['bucket_end'].replace('Z', '+00:00')}
    with pytest.raises(ValueError, match='duplicate'):
        project_alibaba_proxy_events([*windows, duplicate])
    windows = _windows({}, start=259)
    windows[0]['eligible'] = True
    with pytest.raises(ValueError, match='warmup windows must be ineligible'):
        project_alibaba_proxy_events(windows)


def test_cli_reproduces_labels_instead_of_using_calibration_anchor_labels(tmp_path):
    windows = _windows({330: 1}, start=259)
    for row in windows:
        row['y'] = 1
    descriptor, _ = _descriptor(tmp_path, windows)
    output = tmp_path / 'output'
    receipt = run(tmp_path, descriptor=descriptor, output=output)
    events = parquet.read_table(output / 'events.parquet').to_pylist()
    assert receipt['eligible_events'] == 1
    assert [(event['onset_at'], event['positive_minutes']) for event in events] == [(_at(330), 1)]


def test_ineligible_events_supply_inclusive_censor_metadata_to_evaluator():
    windows = _windows({322: 1}, start=259)
    events, healthy = project_alibaba_proxy_events(windows)
    assert events[0]['censor_until'] == _at(327)
    for row in windows:
        row['healthy'] = _key(row) in healthy
        if not row['eligible']:
            row['eligibility_reason'] = 'causal-warmup'
    predictions = [{**dict(zip(KEY_FIELDS, _key(row), strict=True)), 'model_id': model,
                    'score': 2.0 if row['relative_minute'] in (324, 327, 329) else 0.0}
                   for row in windows if row['eligible'] for model in MODELS]
    operating = {'status': 'ready', 'threshold': 1.0, 'thresholds': {'median_mad': 1.0, 'adsketch': 1.0}}
    predictions = append_combo_predictions(predictions, operating)
    result = evaluate_detectors(
        windows, events, predictions, clock='replay', thresholds=dict.fromkeys((*MODELS, COMBO_MODEL), 1.0),
        component_thresholds=operating['thresholds'], policy_identity='fixture',
        persistence={'step_seconds': 60, 'entry_windows': 1, 'exit_windows': 1}, event_kind='alibaba-proxy',
    )
    matches = [row for row in result['matches'] if row['model_id'] == 'median_mad']
    assert [(row['status'], row['emitted_at']) for row in matches] == [
        ('censored', _at(324)), ('censored', _at(327)), ('unmatched', _at(329)),
    ]
    report = result['models']['median_mad']
    assert (report['tp'], report['fp'], report['miss']) == (0, 1, 0)
    assert [(row['status'], row['emitted_at']) for row in result['matches'] if row['model_id'] == COMBO_MODEL] == [
        ('censored', _at(324)), ('censored', _at(327)), ('unmatched', _at(329)),
    ]


@pytest.mark.parametrize(('changes', 'match'), [
    ({'relative_minute': 259, 'bucket_end': _at(259), 'eligible': True}, 'warmup windows must be ineligible'),
    ({'y': 2}, 'label must be'),
    ({'value': float('nan')}, 'finite nonnegative latency'),
])
def test_cli_rejects_invalid_warmup_labels_and_latency(tmp_path, changes, match):
    windows = _windows({}, start=259)
    windows[0].update(changes)
    descriptor, _ = _descriptor(tmp_path, windows)
    with pytest.raises(ValueError, match=match):
        run(tmp_path, descriptor=descriptor, output=tmp_path / 'output')


def test_missing_first_scoring_bucket_never_moves_epoch_or_followup_deadline(tmp_path):
    windows = _windows({348: 1}, start=259, omitted=(324,))
    minutes = [DetectorMinute(SERVICE, int(ORIGIN.timestamp()) + row['relative_minute'] * 60, row['value'])
               for row in windows]
    for row, labelled in zip(windows, proxy_minute_labels(minutes), strict=True):
        row['y'] = labelled['y']
    events, healthy = project_alibaba_proxy_events(windows)
    assert len(events) == 1
    assert (events[0]['onset_at'], events[0]['eligible'], events[0]['reason'], events[0]['censor_until']) == (
        _at(348), False, 'insufficient-follow-up', _at(353),
    )
    assert healthy == {_key(row) for row in windows if 330 <= row['relative_minute'] <= 342}

    without_coordinates = [{field: value for field, value in row.items() if field != 'relative_minute'}
                           for row in windows]
    with pytest.raises(ValueError, match='relative_minute required on every'):
        project_alibaba_proxy_events(without_coordinates)
    with pytest.raises(ValueError, match='relative_minute required on every'):
        project_alibaba_proxy_events([*windows[:-1], without_coordinates[-1]])

    outputs = []
    for name, rows, minute_zero in (('coordinates', windows, None), ('explicit-origin', without_coordinates, _at(0))):
        root = tmp_path / name
        root.mkdir()
        descriptor, _ = _descriptor(root, rows)
        output = root / 'output'
        receipt = run(root, descriptor=descriptor, output=output, minute_zero=minute_zero)
        saved_events = parquet.read_table(output / 'events.parquet').to_pylist()
        saved_windows = parquet.read_table(output / 'windows.parquet').to_pylist()
        assert project_alibaba_proxy_events(saved_windows) == (events, healthy)
        assert saved_events == events
        assert receipt['eligible_events'] == 0
        assert receipt['ineligible_by_reason'] == {'insufficient-follow-up': 1}
        assert receipt['healthy_windows'] == len(healthy)
        outputs.append((saved_events, saved_windows, receipt['events'], receipt['projected_windows']))
    assert outputs[0] == outputs[1]
