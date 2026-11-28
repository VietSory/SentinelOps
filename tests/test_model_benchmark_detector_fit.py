import hashlib
import json
import math

import pytest

from sentinelops.ml.alibaba_training import MINUTE_MS, load_config, load_minutes, split_of
from sentinelops.ml.model_benchmark_detector import DetectorConfig
from sentinelops.ml.model_benchmark_detector_combo import COMBO_MODEL, COMBO_SELECTION_RULE_ID
from sentinelops.ml.model_benchmark_detector_fit import (
    ALIBABA_SCOPE,
    BASELINE_LOOKBACK,
    FIT_PATH,
    SYNTHETIC_SCOPE,
    DetectorMinute,
    _cohorts,
    _protocol_cohort_constraints,
    _verified_aggregates,
    fit_detector_benchmark,
    fit_synthetic_detector_benchmark,
    project_detector_run,
    verify_detector_fit,
    verify_protocol_detector_fit,
)
from sentinelops.public_data.alibaba import AGGREGATE_VERSION, SOURCE
from sentinelops.telemetry.core import stable_id


def _service_names(buckets, count):
    names = []
    for index in range(5000):
        service = f'synthetic-{index}'
        if int(hashlib.sha256(service.encode()).hexdigest(), 16) % 10 in buckets:
            names.append(service)
        if len(names) == count:
            return names
    raise AssertionError('not enough synthetic services')


def _series(service, values, *, start=0):
    return [DetectorMinute(service, (start + index) * 60, value) for index, value in enumerate(values)]


def _cfg():
    return DetectorConfig(60, 5, normal_patterns=2, anomalous_patterns=2, max_fit_windows=48,
                          forest_trees=4, forest_sample_size=16, discovery_quantile=.8)


def _inputs(calibration_count=101):
    reference, = _service_names({0, 1, 2}, 1)
    discovery, = _service_names({3, 4, 5}, 1)
    calibration = _service_names({6, 7}, calibration_count + 1)
    evaluation, = _service_names({8, 9}, 1)
    rows = _series(reference, [10 + (index % 7) / 10 for index in range(45)])
    rows += _series(discovery, [10.0] * 22 + [95.0, 125.0, 105.0, 130.0, 90.0] + [10.0] * 12)
    for service in calibration[:-1]:
        rows += _series(service, [10.0] * 10)
    rows += _series(calibration[-1], [10.0] * 5 + [95.0] * 5)
    rows += _series(evaluation, [10_000.0] * 45)
    return rows


def test_projection_is_causal_and_resets_at_missing_minutes_gaps_and_services():
    prefix = _series('one', [10.0] * 5 + [100.0, None] + [20.0] * 6)
    suffix = _series('one', [500.0] * 6, start=20)
    other = _series('two', [50.0] * 6)
    rows = project_detector_run(reversed(prefix + suffix + other), run_id='run', split='train')
    prefix_rows = project_detector_run(prefix, run_id='run', split='train')

    assert rows[:len(prefix_rows)] == prefix_rows
    assert rows[5].value == pytest.approx(math.log1p(100) - math.log1p(10))
    assert rows[5].label == 1
    assert [row.value for row in rows[6:12]] == [None] * 6
    assert rows[12].value == 0 and rows[12].label == 0
    assert [row.value for row in rows[13:18]] == [None] * 5
    assert [row.value for row in rows[-6:-1]] == [None] * 5
    assert project_detector_run(prefix, run_id='run')[5].label is None
    with pytest.raises(ValueError, match='duplicate service minute'):
        project_detector_run(prefix + prefix[-1:], run_id='run')


@pytest.mark.parametrize('missing', [False, True])
def test_bounded_arrival_projection_matches_full_visible_history(missing):
    minutes = _series('service', [10.0 + index / 10 for index in range(90)])
    if missing:
        minutes[-7] = DetectorMinute(minutes[-7].service, minutes[-7].timestamp, None)
    window_length = _cfg().window_length
    full = project_detector_run(minutes, run_id='run')
    bounded = project_detector_run(minutes[-(BASELINE_LOOKBACK + window_length):], run_id='run')
    assert bounded[-window_length:] == full[-window_length:]
    assert full[-1].value is not None


def test_cohorts_use_official_splits_and_disjoint_calibration_dependencies():
    reference, = _service_names({0, 1, 2}, 1)
    discovery, = _service_names({3, 4, 5}, 1)
    calibration, = _service_names({6, 7}, 1)
    evaluation, = _service_names({8, 9}, 1)
    minutes = _series(reference, [10.0] * 20) + _series(discovery, [10.0] * 20)
    minutes += _series(calibration, [10.0] * 145)
    minutes += _series(calibration, [10.0] * 10, start=200)
    minutes += _series(evaluation, [10.0] * 145)
    source = {'scope': SYNTHETIC_SCOPE}

    rows, anchors, summary = _cohorts(minutes, source, 5)
    assert {role: {row.service for row in items} for role, items in rows.items()} == {
        'reference': {reference}, 'discovery': {discovery}, 'calibration': {calibration}}
    assert all(row.split == split_of(row.service) for items in rows.values() for row in items)
    assert summary['calibration']['segments'] == 2
    assert len({key[0] for key in anchors}) == 2
    assert [key[2] // 60 for key in anchors] == [9, 9 + BASELINE_LOOKBACK + 5, 139, 209]
    assert list(anchors.values()) == [0] * 4
    assert _cohorts(reversed(minutes), source, 5) == (rows, anchors, summary)


def test_protocol_cohorts_exclude_other_times_and_bind_calibration_anchors():
    reference, = _service_names({0, 1, 2}, 1)
    calibration, = _service_names({6, 7}, 1)
    records = [
        dict(service=reference, role='train', parent_split='train', partition_start=0,
             partition_end=19, anchor_minute=15),
        dict(service=calibration, role='calibration', parent_split='calibration', partition_start=20,
             partition_end=59, anchor_minute=35),
        dict(service=calibration, role='development', parent_split='calibration', partition_start=20,
             partition_end=59, anchor_minute=45),
    ]
    constraints = _protocol_cohort_constraints(records)
    minutes = _series(reference, [10.0] * 70) + _series(calibration, [10.0] * 70)
    rows, anchors, summary = _cohorts(minutes, {'scope': SYNTHETIC_SCOPE}, 5, constraints=constraints)
    assert max(row.timestamp // 60 for row in rows['reference']) == 19
    assert [key[2] // 60 for key in anchors] == [35]
    assert max(row.timestamp // 60 for row in rows['calibration']) == 35
    changed = [DetectorMinute(item.service, item.timestamp, 1000.0)
               if (item.service == reference and item.timestamp // 60 > 19)
               or (item.service == calibration and item.timestamp // 60 > 35) else item for item in minutes]
    assert _cohorts(changed, {'scope': SYNTHETIC_SCOPE}, 5, constraints=constraints) == (rows, anchors, summary)
    missing = [item for item in minutes if not (item.service == calibration and item.timestamp == 35 * 60)]
    _, missing_anchors, _ = _cohorts(missing, {'scope': SYNTHETIC_SCOPE}, 5, constraints=constraints)
    assert list(missing_anchors.values()) == [None]
    assert [key[2] // 60 for key in missing_anchors] == [35]


def test_protocol_bound_fit_pins_splits_and_ignores_outside_minutes(tmp_path, monkeypatch):
    from sentinelops.ml import model_benchmark_detector_fit as fitting

    minutes = _inputs()
    services = {item.service for item in minutes if split_of(item.service) != 'evaluation'}
    records = [dict(service=service, role=split_of(service), parent_split=split_of(service),
                    partition_start=0, partition_end=44 if split_of(service) == 'train' else 9,
                    anchor_minute=20 if split_of(service) == 'train' else 9) for service in services]
    splits = json.dumps({'records': records}, sort_keys=True).encode()
    pin = hashlib.sha256(splits).hexdigest()
    source = {'scope': ALIBABA_SCOPE, 'source': SOURCE, 'provenance_sha256': 'a' * 64,
              'archive_count': 1, 'archives_sha256': 'b' * 64}
    current = [minutes]
    monkeypatch.setattr(fitting, '_verified_aggregates', lambda *args: (dict(source), iter(current[0])))
    def fit(root):
        return fit_detector_benchmark(root, aggregate_dir=root, provenance=root, provenance_sha256='a' * 64,
                                      config=_cfg(), protocol_splits=splits, protocol_splits_sha256=pin)
    first = fit(tmp_path / 'first')
    verified = verify_detector_fit(tmp_path / 'first', expected_fit_sha256=first['fit_sha256'])
    assert verified.protocol_splits_sha256 == pin and first['status'] == 'ready'
    verify_protocol_detector_fit(tmp_path / 'first', expected_fit_sha256=first['fit_sha256'],
                                 protocol_splits=splits, protocol_splits_sha256=pin)
    manifest = {'files': [{'path': 'snapshot/source/' + name, 'sha256': digest}
                          for name, digest in verified.fit_code_sha256.items()]}
    verify_protocol_detector_fit(tmp_path / 'first', expected_fit_sha256=first['fit_sha256'],
                                 protocol_splits=splits, protocol_splits_sha256=pin,
                                 protocol_source_manifest=json.dumps(manifest).encode())
    manifest['files'][0]['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='fit code differs'):
        verify_protocol_detector_fit(tmp_path / 'first', expected_fit_sha256=first['fit_sha256'],
                                     protocol_splits=splits, protocol_splits_sha256=pin,
                                     protocol_source_manifest=json.dumps(manifest).encode())
    current[0] = minutes + [DetectorMinute(service, 45 * 60, 9999.0) for service in services]
    second = fit(tmp_path / 'second')
    assert first['model_sha256'] == second['model_sha256']
    assert first['calibration'] == second['calibration']
    calibration_service = next(service for service in services if split_of(service) == 'calibration')
    current[0] = [item for item in minutes if (item.service, item.timestamp) != (calibration_service, 9 * 60)]
    third = fit(tmp_path / 'missing')
    assert all(point['rows'] == 102 and point['missing_scores'] == 1 and point['unknown_windows'] == 1
               for model, point in third['calibration'].items() if model != COMBO_MODEL)
    assert third['calibration'][COMBO_MODEL]['negatives'] == 100
    path = tmp_path / 'first' / FIT_PATH
    artifact = json.loads(path.read_bytes())
    artifact['calibration_rows']['adsketch'][0]['service'] = 'unregistered-service'
    changed_bytes = json.dumps(artifact, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    path.write_bytes(changed_bytes)
    with pytest.raises(ValueError, match='calibration source identity'):
        verify_protocol_detector_fit(tmp_path / 'first', expected_fit_sha256=hashlib.sha256(changed_bytes).hexdigest(),
                                     protocol_splits=splits, protocol_splits_sha256=pin)
    with pytest.raises(ValueError, match='split SHA256 mismatch'):
        fit_detector_benchmark(tmp_path / 'third', aggregate_dir=tmp_path, provenance=tmp_path,
                               provenance_sha256='a' * 64, config=_cfg(), protocol_splits=splits,
                               protocol_splits_sha256='0' * 64)


def test_synthetic_fit_reloads_and_preserves_tied_negative_budget(tmp_path):
    minutes = _inputs()
    receipt = fit_synthetic_detector_benchmark(tmp_path, minutes=minutes, config=_cfg())
    pin = receipt['fit_sha256']
    checkpoint = verify_detector_fit(tmp_path, expected_fit_sha256=pin, allow_synthetic=True)

    assert receipt['status'] == checkpoint.status == 'ready'
    assert checkpoint.scope == SYNTHETIC_SCOPE and checkpoint.provenance_sha256 is None
    assert checkpoint.persistence == 2 and receipt['activation_allowed'] is False
    assert checkpoint.model.digest == receipt['model_sha256']
    assert checkpoint.model.support['normal_segments'] > 0
    assert checkpoint.model.support['anomalous_segments'] > 0
    assert receipt['recovery']['max_abs_delta'] <= 1e-6
    assert receipt['recovery']['scored_predictions'] == 306
    assert all(point['negative_windows'] == 101 and point['positive_windows'] == 1
               and point['negative_flags'] == 0 and point['comparison'] == '>='
               for model, point in checkpoint.calibration.items() if model != COMBO_MODEL)
    combo = checkpoint.calibration[COMBO_MODEL]
    assert (combo['negatives'], combo['positives'], combo['negative_flags'], combo['positive_flags']) == (101, 1, 0, 1)
    assert combo['threshold'] == 1.0 and combo['comparison'] == '>='
    assert combo['thresholds']['median_mad'] > 0 and combo['thresholds']['adsketch'] is None
    assert combo['selection_rule'] == COMBO_SELECTION_RULE_ID and combo['grid_size'] > 0
    assert fit_synthetic_detector_benchmark(tmp_path, minutes=reversed(minutes), config=_cfg()) == receipt
    with pytest.raises(ValueError, match='explicit development'):
        verify_detector_fit(tmp_path, expected_fit_sha256=pin)
    with pytest.raises(ValueError, match='different detector fit'):
        fit_synthetic_detector_benchmark(tmp_path, minutes=minutes, config=_cfg(), persistence=3)


@pytest.mark.parametrize('changed_field', ['status', 'thresholds', 'selection_rule'])
def test_fit_rejects_changed_combo_operating_point_on_round_trip(tmp_path, monkeypatch, changed_field):
    from sentinelops.ml import model_benchmark_detector_fit as fitting

    operating_points = fitting._operating_points
    calls = 0

    def changed_replay(*args):
        nonlocal calls
        points = operating_points(*args)
        calls += 1
        if calls == 2:
            if changed_field == 'thresholds':
                points[COMBO_MODEL]['thresholds']['median_mad'] *= 2
            else:
                points[COMBO_MODEL][changed_field] = 'changed'
        return points

    monkeypatch.setattr(fitting, '_operating_points', changed_replay)
    with pytest.raises(ValueError, match='restored detector operating points changed'):
        fit_synthetic_detector_benchmark(tmp_path, minutes=_inputs(), config=_cfg())
    assert calls == 2
    assert not (tmp_path / FIT_PATH).exists()


def test_calibration_and_evaluation_cannot_change_fitted_model(tmp_path):
    minutes = _inputs(calibration_count=2)
    receipt = fit_synthetic_detector_benchmark(tmp_path / 'original', minutes=minutes, config=_cfg())
    evaluation_changed = [minute if split_of(minute.service) != 'evaluation' else
                          DetectorMinute(minute.service, minute.timestamp, None) for minute in minutes]
    assert fit_synthetic_detector_benchmark(tmp_path / 'original', minutes=evaluation_changed,
                                            config=_cfg()) == receipt
    changed = [minute if split_of(minute.service) != 'calibration' else
               DetectorMinute(minute.service, minute.timestamp, 17.0) for minute in minutes]
    second = fit_synthetic_detector_benchmark(tmp_path / 'changed', minutes=changed, config=_cfg())

    assert receipt['model_sha256'] == second['model_sha256']
    assert receipt['cohorts']['reference'] == second['cohorts']['reference']
    assert receipt['cohorts']['discovery'] == second['cohorts']['discovery']
    assert receipt['cohorts']['calibration'] != second['cohorts']['calibration']
    assert receipt['status'] == second['status'] == 'not-ready'
    assert all(point['threshold'] is None for model, point in second['calibration'].items() if model != COMBO_MODEL)
    assert second['calibration'][COMBO_MODEL]['status'] == 'not-ready'
    assert second['calibration'][COMBO_MODEL]['threshold'] == 1.0


@pytest.mark.parametrize('model, field', [('adsketch', 'threshold'), (COMBO_MODEL, 'thresholds')])
def test_verifier_rejects_changed_bytes_and_inconsistent_operating_point(tmp_path, model, field):
    receipt = fit_synthetic_detector_benchmark(tmp_path, minutes=_inputs(2), config=_cfg())
    path = tmp_path / FIT_PATH
    artifact = json.loads(path.read_bytes())
    artifact['calibration'][model][field] = 0.1
    edited = json.dumps(artifact, sort_keys=True, separators=(',', ':')).encode()
    path.write_bytes(edited)

    with pytest.raises(ValueError, match='byte SHA256'):
        verify_detector_fit(tmp_path, expected_fit_sha256=receipt['fit_sha256'], allow_synthetic=True)
    with pytest.raises(ValueError, match='calibration or recovery receipt'):
        verify_detector_fit(tmp_path, expected_fit_sha256=hashlib.sha256(edited).hexdigest(), allow_synthetic=True)


def _source_files(tmp_path, config, shards):
    archives = []
    for stem, parquet in shards.items():
        archive = {'archive': stem + '.tar.gz', 'archive_sha256': 'a' * 64,
                   'parquet_sha256': hashlib.sha256(parquet).hexdigest()}
        meta = {'source': SOURCE, 'aggregate_version': AGGREGATE_VERSION, **archive}
        (tmp_path / (stem + '.json')).write_text(json.dumps(meta))
        (tmp_path / (stem + '.parquet')).write_bytes(parquet)
        archives.append(archive)
    report = {'report_version': 'alibaba-relative-development/v2', 'authorizing_adr': 'ADR-019',
              'config': config, 'config_sha256': stable_id(config), 'source_archives': archives}
    provenance_path = tmp_path / 'synthetic-report.json'
    content = json.dumps(report).encode()
    provenance_path.write_bytes(content)
    return provenance_path, hashlib.sha256(content).hexdigest()


def test_fit_refuses_unpinned_reports_invalid_splits_and_changed_aggregate_bytes(tmp_path):
    config = load_config()
    shards = {'MCRRTUpdate_42': b'synthetic bytes for provenance rejection tests'}
    provenance, pin = _source_files(tmp_path, config, shards)
    with pytest.raises(ValueError, match='provenance byte SHA256 mismatch'):
        fit_detector_benchmark(tmp_path, aggregate_dir=tmp_path, provenance=provenance,
                               provenance_sha256='f' * 64, config=_cfg())

    meta_path = tmp_path / 'MCRRTUpdate_42.json'
    original = json.loads(meta_path.read_bytes())
    meta_path.write_text(json.dumps({**original, 'source': 'synthetic'}))
    with pytest.raises(ValueError, match='aggregate provenance mismatch'):
        fit_detector_benchmark(tmp_path, aggregate_dir=tmp_path, provenance=provenance,
                               provenance_sha256=pin, config=_cfg())
    meta_path.write_text(json.dumps(original))
    (tmp_path / 'MCRRTUpdate_42.parquet').write_bytes(b'changed')
    with pytest.raises(ValueError, match='aggregate byte SHA256 mismatch'):
        fit_detector_benchmark(tmp_path, aggregate_dir=tmp_path, provenance=provenance,
                               provenance_sha256=pin, config=_cfg())

    config['splits']['assignment'] = 'all-train'
    provenance, pin = _source_files(tmp_path, config, shards)
    with pytest.raises(ValueError, match='official service split'):
        _verified_aggregates(tmp_path, provenance, pin)
    assert not (tmp_path / FIT_PATH).exists()


def test_reader_collapses_overlapping_shards_and_excludes_evaluation_values(tmp_path):
    import pandas as pd

    train, = _service_names({0}, 1)
    evaluation, = _service_names({9}, 1)
    columns = ['timestamp', 'msname', 'http_rt_ms', 'http_mcr_sum']
    frames = {'MCRRTUpdate_12': [(60000, train, 3.0, 10), (60000, evaluation, -1, 10),
                                (120000, train, 0.1, 10), (180000, train, None, 10), (240000, train, None, 10)],
              'MCRRTUpdate_10': [(120000, train, 0.1, 10), (180000, train, 4.0, 5)],
              'MCRRTUpdate_2': [(60000, train, 9.0, 1), (120000, train, 1e15, 1)]}
    shards = {name: pd.DataFrame(rows, columns=columns).to_parquet(index=False)
              for name, rows in frames.items()}
    provenance, pin = _source_files(tmp_path, load_config(), shards)
    _, minutes = _verified_aggregates(tmp_path, provenance, pin)
    official, _ = load_minutes(tmp_path)
    expected = [DetectorMinute(row.msname, row.minute * 60, None if pd.isna(row.http_rt_ms) else row.http_rt_ms)
                for row in official.itertuples(index=False) if split_of(row.msname) != 'evaluation']

    assert list(minutes) == expected
    assert expected[0] == DetectorMinute(train, 60, 6.0)
    assert expected[-2:] == [DetectorMinute(train, 180, 4.0), DetectorMinute(train, 240, None)]
    assert not (tmp_path / FIT_PATH).exists()


def test_fit_retains_pinned_provenance_and_replays_parquet_fixture(tmp_path):
    import pandas as pd

    minutes = _inputs()
    frame = pd.DataFrame(((minute.timestamp // 60 * MINUTE_MS, minute.service, minute.latency_ms)
                          for minute in minutes), columns=['timestamp', 'msname', 'http_rt_ms'])
    provenance, pin = _source_files(tmp_path, load_config(), {'MCRRTUpdate_2': frame.to_parquet(index=False)})
    receipt = fit_detector_benchmark(tmp_path, aggregate_dir=tmp_path, provenance=provenance,
                                    provenance_sha256=pin, config=_cfg())
    checkpoint = verify_detector_fit(tmp_path, expected_fit_sha256=receipt['fit_sha256'])

    assert receipt['scope'] == checkpoint.scope == ALIBABA_SCOPE
    assert receipt['provenance_sha256'] == checkpoint.provenance_sha256 == pin
    assert receipt['status'] == checkpoint.status == 'ready'
    assert receipt['calibration'] == checkpoint.calibration
    assert receipt['model_sha256'] == checkpoint.model.digest
    assert receipt['recovery'] == {'method': 'completed-model-json-round-trip', 'scored_predictions': 306,
                                   'max_abs_delta': 0.0, 'tolerance': 1e-6}
    artifact = json.loads((tmp_path / FIT_PATH).read_bytes())
    assert artifact['identity']['source']['archive_count'] == 1
    assert fit_detector_benchmark(tmp_path, aggregate_dir=tmp_path, provenance=provenance,
                                  provenance_sha256=pin, config=_cfg()) == receipt
