"""Development report artifacts and runbook command guards."""

import json
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
        'predictions': [{'group_id': 'ald-01', 'run_id': 'run-one', 'value': 0.5}],
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
    matches = parquet.read_table(output / 'matches.parquet').to_pylist()
    assert len(matches) == 3 and all(match['detection_delay_seconds'] == 120 for match in matches)
    assert all(match['reason'] is None and match['status'] == 'matched' for match in matches)


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
