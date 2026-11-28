"""Development report artifacts and runbook command guards."""

import json
import csv
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.model_benchmark import main
from sentinelops.ml.model_benchmark_detector_evaluation import MODELS, evaluate_detectors
from sentinelops.ml.model_benchmark_detector_stage import _sha, _verify_report, _write_report


def test_detector_cli_waits_for_input_without_opening_capture(tmp_path, capsys):
    root = tmp_path / 'root'
    result = main(['--root', str(root), '--protocol', str(tmp_path / 'protocol.json'),
                   'score', '--lane', 'b', '--lane-b-input', str(tmp_path / 'missing.json')])
    assert result == 2
    assert json.loads(capsys.readouterr().out)['reasons'] == ['lane-b-input-descriptor-required']
    descriptor = tmp_path / 'input.json'
    descriptor.write_text(json.dumps({
        'schema_version': 'detector-score-input/development-v1',
        'dataset': 'sealed-final', 'clock': 'replay', 'fit_sha256': '0' * 64,
        'windows': {}, 'events': {}, 'admission': {},
    }))
    result = main(['--root', str(root), '--protocol', str(tmp_path / 'protocol.json'),
                   'score', '--lane', 'b', '--lane-b-input', str(descriptor)])
    assert result == 1
    output = json.loads(capsys.readouterr().out)
    assert output['status'] == 'invalid'
    assert output['reasons'] == ['unsupported detector development dataset or clock']
    assert not root.exists()


def test_report_is_immutable_and_reconciles_its_csv(tmp_path):
    output = tmp_path / 'report'
    identity = {'fixture': True}
    result = {
        'dataset': 'local-development', 'clock': 'replay',
        'models': {'adsketch': {'precision': 0.75, 'recall': 0.6},
                   'median_mad': {'precision': None, 'recall': 0.0},
                   'isolation_forest': {'precision': 0.5, 'recall': 1.0}},
        'predictions': [{'group_id': 'ald-01', 'run_id': 'run-one', 'score': 0.5}],
        'events': [], 'matches': [],
    }
    first = _write_report(output, identity, result)
    assert _write_report(output, identity, result) == first
    assert _verify_report(output, identity, reevaluate=lambda _: result) == first
    assert len((output / 'detector.csv').read_text().splitlines()) == 4
    with pytest.raises(ValueError, match='identity'):
        _verify_report(output, {'fixture': False})
    (output / 'detector.csv').write_text('forged report\n')
    with pytest.raises(ValueError, match='SHA-256'):
        _verify_report(output, identity)
    manifest = output / 'manifest.json'
    changed = json.loads(manifest.read_bytes())
    changed['files']['detector.csv'] = _sha(output / 'detector.csv')
    manifest.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='reconcile'):
        _verify_report(output, identity, reevaluate=lambda _: result)


def test_report_reloads_oracle_and_matches_with_a_fixed_parquet_schema(tmp_path):
    import pyarrow as arrow
    import pyarrow.parquet as parquet

    output = tmp_path / 'report'
    identity = {'fixture': True}
    match_key = {'dataset': 'local-development', 'group_id': 'ald-01',
                 'run_id': 'run-one', 'service': 'payment-service'}
    window_key = {**match_key, 'bucket_end': '2026-11-25T05:02:00Z'}
    windows = [{**window_key, 'eligible': True, 'y': 1, 'healthy': False}]
    events = [{**match_key, 'event_id': 'event-one', 'kind': 'latency', 'eligible': True,
               'onset_at': '2026-11-25T05:00:00Z', 'confirmed_at': '2026-11-25T05:02:00Z'}]
    predictions = [{**window_key, 'model_id': model, 'score': 0.9} for model in MODELS]

    def evaluate(rows):
        return evaluate_detectors(windows, events, rows, clock='replay', thresholds=dict.fromkeys(MODELS, 0.5),
                                  policy_identity='test-policy',
                                  persistence={'step_seconds': 60, 'entry_windows': 1, 'exit_windows': 1})

    report = _write_report(output, identity, evaluate(predictions))
    assert _verify_report(output, identity, reevaluate=evaluate) == report
    prediction_schema = parquet.read_schema(output / 'predictions.parquet')
    assert all(prediction_schema.field(name).type == arrow.string() for name in ('state', 'episode_id', 'emitted_at'))
    matches = parquet.read_table(output / 'matches.parquet').to_pylist()
    assert len(matches) == 3 and all(match['detection_delay_seconds'] == 120 for match in matches)
    assert all(match['reason'] is None and match['status'] == 'matched' for match in matches)


@pytest.mark.parametrize('ledger', ['predictions', 'events', 'matches'])
@pytest.mark.parametrize('change', ['column-name', 'column-order', 'type', 'nullability'])
def test_report_rejects_schema_changes_even_with_updated_manifest(tmp_path, ledger, change):
    import pyarrow as arrow
    import pyarrow.parquet as parquet

    identity = {'fixture': True}
    key = {'dataset': 'local-development', 'group_id': 'ald-01', 'run_id': 'run-one', 'service': 'payment-service'}
    result = {
        'dataset': 'local-development', 'clock': 'replay', 'models': {'median_mad': {'recall': 1.0}},
        'predictions': [{**key, 'model_id': 'median_mad', 'score': 0.5}],
        'events': [{**key, 'event_id': 'event-one', 'length_minutes': 1, 'positive_minutes': 1}],
        'matches': [{**key, 'event_id': 'event-one', 'model_id': 'median_mad', 'detection_delay_seconds': 120.0}],
    }
    output = tmp_path / 'report'
    _write_report(output, identity, result)
    path = output / (ledger + '.parquet')
    table = parquet.read_table(path)
    original_rows = table.to_pylist()
    if change == 'column-name':
        table = table.rename_columns(['renamed_column', *table.column_names[1:]])
    elif change == 'column-order':
        table = table.select(list(reversed(table.column_names)))
    else:
        column = {'predictions': 'score', 'events': 'length_minutes', 'matches': 'detection_delay_seconds'}[ledger]
        position = table.schema.get_field_index(column)
        field = table.schema.field(position)
        replacement = (field.with_type(arrow.int32() if ledger == 'events' else arrow.float32())
                       if change == 'type' else field.with_nullable(False))
        table = table.cast(table.schema.set(position, replacement))
    if change != 'column-name':
        assert table.to_pylist() == original_rows
    parquet.write_table(table, path)
    manifest_path = output / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files'][path.name] = _sha(path)
    manifest_path.write_text(json.dumps(manifest))
    for reevaluate in (None, lambda _: result):
        with pytest.raises(ValueError, match=ledger + r'\.parquet Arrow schema differs'):
            _verify_report(output, identity, reevaluate=reevaluate)


def test_prepare_binds_pinned_projection_and_retries(tmp_path, monkeypatch):
    from sentinelops.ml import model_benchmark_detector_archive as archive
    from sentinelops.ml import model_benchmark_detector_fit as fit

    dest = tmp_path / 'bench'
    source = tmp_path / 'archive'
    source.mkdir()
    checkpoint = dest / fit.FIT_PATH
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text('test double')
    monkeypatch.setattr(fit, 'verify_detector_fit', lambda *args, **kwargs: SimpleNamespace(status='ready'))

    admission = {'dataset_version': 'alibaba-local-evaluation-admission/v1',
                 'evaluation_ready': True, 'holdout_opened': False, 'created_at': 'fixture-clock',
                 'source_result_refs': ['fixture-capture'], 'oracle_certificate_refs': ['fixture-oracle']}
    monkeypatch.setattr(archive, 'audit_alibaba_local', lambda *args: (admission, [], [{}]))
    monkeypatch.setattr(archive, '_project_case', lambda *args: ([{'value': 1.0}], []))
    first = archive.prepare_detector_archive(dest, source, '0' * 64)
    descriptor = json.loads(Path(first['descriptor']).read_bytes())
    receipt = dest / descriptor['admission']['path']
    proof = json.loads(receipt.read_bytes())
    assert _sha(receipt) == descriptor['admission']['sha256']
    assert proof['schema_version'] == 'detector-projection/development-v1'
    assert all(proof[key] == descriptor[key] for key in ('dataset', 'clock', 'windows', 'events'))
    assert proof['source_identity']['oracle_certificate_refs'] == ['fixture-oracle']
    admission['created_at'] = 'fixture-retry-clock'
    assert archive.prepare_detector_archive(dest, source, '0' * 64) == first


@pytest.mark.parametrize('clock', ['replay', 'arrival-aware'])
@pytest.mark.parametrize('wrong_event_rule', [False, True])
def test_stage_frozen_combo_scores_replay_and_report_bytes(tmp_path, monkeypatch, clock, wrong_event_rule):
    import pyarrow as arrow
    import pyarrow.parquet as parquet
    from test_model_benchmark_detector_fit import _cfg, _inputs
    from sentinelops.ml import model_benchmark_detector_fit as fitting
    from sentinelops.ml import model_benchmark_detector_stage as stage
    from sentinelops.ml.alibaba_training import split_of
    from sentinelops.ml.model_benchmark_alibaba_events import (
        ALIBABA_EVENT_RULE_ID, project_alibaba_proxy_events, proxy_minute_labels,
    )
    from sentinelops.ml.model_benchmark_detector_combo import (
        COMBO_MODEL, COMBO_SELECTION_RULE_ID, combo_score,
    )
    from sentinelops.ml.model_benchmark_detector_evaluation import KEY_FIELDS
    from sentinelops.ml.model_benchmark_protocol import lane_b_rules
    from sentinelops.public_data.alibaba import SOURCE
    from sentinelops.telemetry.core import timestamp

    def ref(name, value):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(stage._json_bytes(value))
        return {'path': name, 'sha256': _sha(path)}

    minutes = _inputs()
    services = {minute.service for minute in minutes if split_of(minute.service) != 'evaluation'}
    records = [dict(service=service, role=split_of(service), parent_split=split_of(service),
                    partition_start=0, partition_end=44 if split_of(service) == 'train' else 9,
                    anchor_minute=20 if split_of(service) == 'train' else 9) for service in sorted(services)]
    manifest = ref('data.json', {'fixture': 'synthetic-development-only'})
    code_root = Path(__file__).resolve().parents[1]
    source_files = []
    for name in fitting._FIT_CODE_PATHS:
        path = tmp_path / 'snapshot/source' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((code_root / name).read_bytes())
        source_files.append({'path': str(path.relative_to(tmp_path)), 'sha256': _sha(path)})
    protocol = {
        'data': {'manifest': manifest, 'splits': ref('splits.json', {'records': records})},
        'code': {'source_manifest': ref('source.json', {'files': source_files})},
        'training': {'seed': 42}, 'scoring': {'step_seconds': 60},
        'reporting': {'lane_b': lane_b_rules(ALIBABA_EVENT_RULE_ID, COMBO_SELECTION_RULE_ID)},
    }
    protocol_path = tmp_path / 'protocol.json'
    protocol_path.write_bytes(stage._json_bytes(protocol))
    ref('freeze/protocol.json', {'protocol': protocol})
    audit = {'lanes': {'b': {'status': 'ready', 'reasons': []}}, 'protocol_sha256': _sha(protocol_path)}
    monkeypatch.setattr(stage, 'audit_protocol', lambda *args: audit)
    monkeypatch.setattr(stage, 'verify_protocol', lambda *args: audit)
    source = {'scope': fitting.ALIBABA_SCOPE, 'source': SOURCE, 'provenance_sha256': manifest['sha256'],
              'archive_count': 1, 'archives_sha256': 'b' * 64}
    monkeypatch.setattr(fitting, '_verified_aggregates', lambda *args: (dict(source), iter(minutes)))
    fit_spec = ref('fit-input.json', {
        'schema_version': 'detector-fit-input/development-v1', 'aggregate_dir': 'fixture-aggregates',
        'provenance': manifest, 'config': asdict(_cfg()), 'persistence': 1,
    })
    fitted = stage.run_detector_stage('fit', tmp_path, protocol_path, tmp_path / fit_spec['path'])
    assert fitted['status'] == 'ready'
    calibrated_spec = ref('calibration-input.json', {
        'schema_version': 'detector-calibration-input/development-v1', 'fit_sha256': fitted['fit_sha256'],
    })
    calibrated = stage.run_detector_stage('calibrate', tmp_path, protocol_path, tmp_path / calibrated_spec['path'])
    point = calibrated['calibration'][COMBO_MODEL]
    solo_points = {model: calibrated['calibration'][model]['threshold'] for model in ('median_mad', 'adsketch')}
    assert calibrated['status'] == 'ready' and point['thresholds'] != solo_points
    assert point['thresholds']['adsketch'] is None

    service = next(f'fixture-evaluation-{index}' for index in range(1000)
                   if split_of(f'fixture-evaluation-{index}') == 'evaluation')
    start = datetime(2026, 1, 1, tzinfo=UTC)
    values = {minute: 95.0 if minute in (331, 332, 350, 351) else 10.0 for minute in range(259, 353)}
    values.update(dict.fromkeys(range(324, 329), 12.0))
    labels = proxy_minute_labels([fitting.DetectorMinute(service, minute * 60, value)
                                 for minute, value in values.items()])
    windows = [dict(dataset='alibaba-proxy', group_id=service, run_id=service, service=service,
                    bucket_end=timestamp(start + timedelta(minutes=label['timestamp'] // 60)),
                    available_at=timestamp(start + timedelta(minutes=label['timestamp'] // 60)),
                    decision_at=timestamp(start + timedelta(minutes=label['timestamp'] // 60)),
                    relative_minute=label['timestamp'] // 60, y=label['y'],
                    eligible=label['timestamp'] // 60 >= 324,
                    eligibility_reason='causal-warmup' if label['timestamp'] // 60 < 324 else None,
                    healthy=False, value=values[label['timestamp'] // 60]) for label in labels]
    events, healthy = project_alibaba_proxy_events(windows)
    for window in windows:
        window['healthy'] = tuple(window[field] for field in KEY_FIELDS) in healthy
    projected = {}
    projection_directory = tmp_path / 'projection'
    projection_directory.mkdir()
    for name, rows in (('windows', windows), ('events', events)):
        path = projection_directory / (name + '.parquet')
        parquet.write_table(arrow.Table.from_pylist(rows), path)
        projected[name] = {'path': str(path.relative_to(tmp_path)), 'sha256': _sha(path)}
    admission = ref('admission.json', {
        'schema_version': 'detector-projection/development-v1', 'dataset': 'alibaba-proxy', 'clock': clock,
        **projected, 'holdout_opened': False, 'source_identity': {'data_manifest_sha256': manifest['sha256']},
    })
    receipt = {
        'schema_version': 'alibaba-detector-event-projection/v1',
        'rule_id': 'f' * 64 if wrong_event_rule else ALIBABA_EVENT_RULE_ID,
        'projected_windows': {**projected['windows'], 'path': 'windows.parquet'},
        'events': {**projected['events'], 'path': 'events.parquet'},
    }
    event_projection = ref('projection/receipt.json', receipt)
    score_spec = ref('score-input.json', {
        'schema_version': 'detector-score-input/development-v1', 'dataset': 'alibaba-proxy', 'clock': clock,
        'fit_sha256': fitted['fit_sha256'], **projected, 'admission': admission, 'event_projection': event_projection,
    })
    score_path = tmp_path / score_spec['path']
    report = stage.run_detector_stage('score', tmp_path, protocol_path, score_path)
    output = Path(report['report_directory'])
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    assert stage.run_detector_stage('report', tmp_path, protocol_path, score_path) == report
    assert stage.run_detector_stage('verify-report', tmp_path, protocol_path, score_path) == report
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    summary = json.loads(before['summary.json'])
    assert summary['event_rule_id'] == receipt['rule_id']
    assert summary['combo_selection_rule_id'] == point['selection_rule']
    assert summary['recommendation']['reason'] == ('rules-unverified' if wrong_event_rule else 'insufficient-event-support')
    assert summary['recommendation']['release'] == 'KEEP'
    assert summary['novel_pattern'] == {'status': 'NA', 'reason': 'insufficient-support'}
    assert all(metrics['patterns']['novel']['status'] == 'NA' and metrics['patterns']['novel']['recall'] is None
               for metrics in summary['models'].values())
    assert 'mad_at_combo_threshold' in summary['adsketch_increment']
    assert summary['adsketch_increment']['attributable_matched_events'] == []
    csv_rows = list(csv.DictReader(before['detector.csv'].decode().splitlines()))
    assert len(csv_rows) == 4
    assert next(row['model_name'] for row in csv_rows if row['model_id'] == 'adsketch') == (
        'ADSketch, biến thể tìm kiếm giới hạn')
    assert all(row['novel_pattern_status'] == 'NA' and row['novel_pattern_reason'] == 'insufficient-support'
               for row in csv_rows)
    persisted_events = parquet.read_table(output / 'events.parquet')
    assert persisted_events.schema.field('length_minutes').type == arrow.int64()
    assert persisted_events.schema.field('positive_minutes').type == arrow.int64()
    assert len(persisted_events) == 2 and persisted_events['censor_until'].null_count == 1
    assert any(row['status'] == 'censored' for row in parquet.read_table(output / 'matches.parquet').to_pylist())
    predictions = parquet.read_table(output / 'predictions.parquet').to_pylist()
    by_key = {(row['bucket_end'], row['model_id']): row for row in predictions}
    combo = [row for row in predictions if row['model_id'] == COMBO_MODEL]
    assert len(combo) == len(windows)
    assert {row['component_availability'] for row in combo} == {'both', 'none'}
    assert all(row['component_availability'] is None for row in predictions if row['model_id'] != COMBO_MODEL)
    differs_from_solo = False
    for row in combo:
        mad, ads = (by_key[row['bucket_end'], model]['score'] for model in ('median_mad', 'adsketch'))
        assert row['score'] == combo_score(mad, ads, point['thresholds'])
        differs_from_solo |= row['score'] != combo_score(mad, ads, solo_points)
    assert differs_from_solo
    admitted_spec = json.loads(score_path.read_bytes())
    missing_projection = {key: value for key, value in admitted_spec.items() if key != 'event_projection'}
    invalid = ref('missing-projection.json', missing_projection)
    with pytest.raises(ValueError, match='input fields'):
        stage.run_detector_stage('score', tmp_path, protocol_path, tmp_path / invalid['path'])
    invalid = ref('invalid-projection-sha.json', {
        **admitted_spec, 'event_projection': {**event_projection, 'sha256': '0' * 64},
    })
    with pytest.raises(ValueError, match='SHA-256'):
        stage.run_detector_stage('score', tmp_path, protocol_path, tmp_path / invalid['path'])
    for field, name in (('projected_windows', 'windows'), ('events', 'events')):
        path = projection_directory / ('other-' + name + '.parquet')
        path.write_bytes((tmp_path / projected[name]['path']).read_bytes())
        other_projection = ref('projection/other-receipt.json', {
            **receipt, field: {'path': path.name, 'sha256': _sha(path)},
        })
        invalid = ref('mismatched-projection.json', {**admitted_spec, 'event_projection': other_projection})
        with pytest.raises(ValueError, match='projection differs'):
            stage.run_detector_stage('score', tmp_path, protocol_path, tmp_path / invalid['path'])
    events_path = output / 'events.parquet'
    event_table = parquet.read_table(events_path)
    position = event_table.schema.get_field_index('length_minutes')
    narrowed = event_table.schema.set(position, event_table.schema.field(position).with_type(arrow.int32()))
    parquet.write_table(event_table.cast(narrowed), events_path)
    manifest_path = output / 'manifest.json'
    forged_manifest = json.loads(before['manifest.json'])
    forged_manifest['files']['events.parquet'] = _sha(events_path)
    manifest_path.write_bytes(stage._json_bytes(forged_manifest))
    with pytest.raises(ValueError, match=r'events\.parquet Arrow schema differs'):
        stage.run_detector_stage('verify-report', tmp_path, protocol_path, score_path)
    events_path.write_bytes(before['events.parquet'])
    manifest_path.write_bytes(before['manifest.json'])
    altered = next(row for row in predictions if row['model_id'] == COMBO_MODEL and row['score'] is not None)
    altered['score'] += 0.25
    parquet.write_table(arrow.Table.from_pylist(predictions, schema=parquet.read_schema(output / 'predictions.parquet')),
                        output / 'predictions.parquet')
    forged_manifest = json.loads(manifest_path.read_bytes())
    forged_manifest['files']['predictions.parquet'] = _sha(output / 'predictions.parquet')
    manifest_path.write_bytes(stage._json_bytes(forged_manifest))
    with pytest.raises(ValueError, match='predictions differ'):
        stage.run_detector_stage('verify-report', tmp_path, protocol_path, score_path)
