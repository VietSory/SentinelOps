"""Lane B development stages with pinned inputs and independently verified reports."""

import csv
import hashlib
import io
from itertools import chain
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from sentinelops.ml.alibaba_training import split_of
from sentinelops.ml.model_benchmark_protocol import audit_protocol, verify_protocol


_TABLES = ('predictions', 'events', 'matches')
_BASELINE_FIT_SHA256 = '58a30c257fb6e181215c600dd1385faa16f5bc15ccacb35df87db69e6838f315'
_MATCH_TEXT = ('dataset group_id run_id service model_id clock event_id event_pattern_kind onset_at '
               'deadline_at event_oracle_ref bucket_end raw_ref oracle_ref episode_id emitted_at '
               'pattern_kind pattern_id status reason').split()
_EVENT_TEXT = ('dataset group_id run_id service event_id onset_at confirmed_at kind reason '
               'oracle_ref pattern_kind deadline_at').split()


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')) + '\n').encode()


def _read_json(path):
    def unique_object(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError('duplicate key in Lane B input')
        return value

    value = json.loads(path.read_bytes(), object_pairs_hook=unique_object)
    _json_bytes(value)
    return value


def _require_fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        raise ValueError('Lane B input fields must be: ' + fields)


def _within(root, name):
    relative = Path(name)
    if relative.is_absolute() or not relative.parts or '..' in relative.parts:
        raise ValueError('Lane B requires a relative artifact path')
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Lane B artifact escapes benchmark root')
    return path


def _sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _pinned(root, ref):
    _require_fields(ref, 'path sha256')
    path = _within(root, ref['path'])
    if not re.fullmatch(r'[0-9a-f]{64}', ref['sha256']) or _sha(path) != ref['sha256']:
        raise ValueError('Lane B artifact SHA-256 differs: ' + ref['path'])
    return path


def _blocked(*reasons):
    return {'status': 'blocked', 'reasons': list(reasons)}


def _calibration_status(status, points):
    if status == 'ready':
        return {'status': 'ready'}
    return _blocked(*(f"{model}: {point['missing_reason']}" for model, point in points.items()
                      if point['status'] != 'ready'))


def _fit(root, protocol_path, spec):
    from sentinelops.ml.model_benchmark_detector import DetectorConfig
    from sentinelops.ml.model_benchmark_detector_fit import fit_detector_benchmark
    from sentinelops.ml.model_benchmark_protocol import _ref

    _require_fields(spec, 'schema_version aggregate_dir provenance config persistence')
    if spec['schema_version'] != 'detector-fit-input/development-v1':
        raise ValueError('Lane B fit requires an Alibaba development input descriptor')
    if not protocol_path.is_file():
        return _blocked('development-protocol-required')
    protocol = _read_json(protocol_path)
    audit = audit_protocol(protocol, root)
    lane = audit['lanes']['b']
    provenance_sha256 = spec['provenance']['sha256']
    if provenance_sha256 not in {_BASELINE_FIT_SHA256, protocol['data']['manifest']['sha256']}:
        raise ValueError('Alibaba fit source must be pinned by the runbook or the development protocol')
    provenance = _pinned(root, spec['provenance'])
    config = DetectorConfig(**spec['config'])
    if config.seed != protocol['training']['seed'] or config.bucket_seconds != protocol['scoring']['step_seconds']:
        raise ValueError('Lane B detector seed and bucket width must match the protocol')
    try:
        receipt = fit_detector_benchmark(
            root, aggregate_dir=_within(root, spec['aggregate_dir']),
            provenance=provenance, provenance_sha256=provenance_sha256,
            config=config, persistence=spec['persistence'],
            protocol_splits=_ref(root, protocol['data']['splits']),
            protocol_splits_sha256=protocol['data']['splits']['sha256'],
        )
    except FileNotFoundError as error:
        return _blocked('required fit artifact missing: ' + str(error.filename))
    operating = _calibration_status(receipt['status'], receipt['calibration'])
    if lane['status'] != 'ready':
        operating = _blocked(*lane['reasons'], *operating.get('reasons', []))
    return {**receipt, **operating, 'fit_status': receipt['status'],
            'protocol_sha256': audit['protocol_sha256'],
            'protocol_status': lane['status'], 'protocol_reasons': lane['reasons']}


def _calibrate(root, spec):
    from sentinelops.ml.model_benchmark_detector_fit import FIT_PATH, verify_detector_fit

    _require_fields(spec, 'schema_version fit_sha256')
    if spec['schema_version'] != 'detector-calibration-input/development-v1':
        raise ValueError('Lane B calibration requires a pinned fit receipt')
    if not (root / FIT_PATH).is_file():
        return _blocked('verified-detector-fit-required')
    fit = verify_detector_fit(root, expected_fit_sha256=spec['fit_sha256'])
    return {**_calibration_status(fit.status, fit.calibration),
            'scope': fit.scope, 'fit_sha256': fit.fit_sha256,
            'calibration': fit.calibration, 'persistence': fit.persistence}


def _verify_report(output, identity, *, reevaluate=None):
    import pyarrow.parquet as parquet

    manifest = _read_json(output / 'manifest.json')
    _require_fields(manifest, 'schema_version identity rows groups runs files')
    if manifest['schema_version'] != 'detector-report/development-v1' or manifest['identity'] != identity:
        raise ValueError('Lane B report identity differs from admitted inputs')
    files = manifest['files']
    expected = {'predictions.parquet', 'events.parquet', 'matches.parquet', 'summary.json', 'detector.csv'}
    if set(files) != expected:
        raise ValueError('Lane B report file inventory differs')
    for name, digest in files.items():
        _pinned(output, {'path': name, 'sha256': digest})
    if set(manifest['rows']) != set(_TABLES) or any(
        parquet.ParquetFile(output / (name + '.parquet')).metadata.num_rows != manifest['rows'][name]
        for name in _TABLES
    ):
        raise ValueError('Lane B report row counts differ')
    if reevaluate is not None:
        tables = {name: parquet.read_table(output / (name + '.parquet')).to_pylist() for name in _TABLES}
        result = reevaluate(tables['predictions'])
        summary = {name: value for name, value in result.items() if name not in _TABLES}
        if (any(_ledger_table(name, _table_rows(result[name])).to_pylist() != rows for name, rows in tables.items())
                or _json_bytes(summary) != (output / 'summary.json').read_bytes()
                or _detector_csv(result) != (output / 'detector.csv').read_bytes()
                or _counts(tables['predictions']) != (manifest['groups'], manifest['runs'])):
            raise ValueError('Lane B report does not reconcile with the pinned inputs and ledgers')
    return {'status': 'ready', 'scope': 'development', 'report_directory': str(output),
            'manifest_sha256': _sha(output / 'manifest.json')}


def _counts(rows):
    return (len({row['group_id'] for row in rows}),
            len({(row['group_id'], row['run_id']) for row in rows}))


def _table_rows(rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    return [{key: row.get(key) for key in columns} for row in rows]


def _ledger_table(name, rows):
    import pyarrow as arrow

    if name == 'predictions':
        return arrow.Table.from_pylist(rows)
    text = _MATCH_TEXT if name == 'matches' else _EVENT_TEXT
    trailing = ([('detection_delay_seconds', arrow.float64())] if name == 'matches' else
                [('eligible', arrow.bool_()), ('in_scope', arrow.bool_())])
    schema = arrow.schema([(key, arrow.string()) for key in text] + trailing)
    extras = set().union(*(row.keys() for row in rows)) - set(schema.names)
    if extras:
        raise ValueError('unrecognized detector ledger fields: ' + ', '.join(sorted(extras)))
    return arrow.Table.from_pylist(rows, schema=schema)


def _detector_csv(result):
    rows = [{'model_id': name, 'dataset': result['dataset'], 'clock': result['clock'],
             **{key: value for key, value in metrics.items() if not isinstance(value, (dict, list, tuple))}}
            for name, metrics in sorted(result['models'].items())]
    columns = list(dict.fromkeys(key for row in rows for key in row))
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def _write_report(output, identity, result):
    import pyarrow.parquet as parquet

    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.detector-', dir=output.parent) as scratch:
        directory = Path(scratch) / 'report'
        directory.mkdir()
        tables = {name: _table_rows(result[name]) for name in _TABLES}
        for name, rows in tables.items():
            parquet.write_table(_ledger_table(name, rows), directory / (name + '.parquet'))
        summary = {name: value for name, value in result.items() if name not in tables}
        (directory / 'summary.json').write_bytes(_json_bytes(summary))
        (directory / 'detector.csv').write_bytes(_detector_csv(result))
        groups, runs = _counts(tables['predictions'])
        manifest = {'schema_version': 'detector-report/development-v1', 'identity': identity,
                    'rows': {name: len(rows) for name, rows in tables.items()},
                    'groups': groups, 'runs': runs,
                    'files': {path.name: _sha(path) for path in sorted(directory.iterdir())}}
        (directory / 'manifest.json').write_bytes(_json_bytes(manifest))
        try:
            directory.rename(output)
        except OSError:
            # Another invocation may already have published the same immutable report.
            if not output.is_dir():
                raise
    return _verify_report(output, identity)


def _score(command, root, spec):
    _require_fields(spec, 'schema_version dataset clock fit_sha256 windows events admission')
    if spec['schema_version'] != 'detector-score-input/development-v1':
        raise ValueError('Lane B scoring requires an admitted development projection')
    dataset, clock = spec['dataset'], spec['clock']
    if dataset not in ('alibaba-proxy', 'local-development') or clock not in ('replay', 'arrival-aware'):
        raise ValueError('unsupported detector development dataset or clock')
    freeze = root / 'freeze/protocol.json'
    if not freeze.is_file():
        return _blocked('verified-shared-development-protocol-freeze-required')
    verified = verify_protocol(root)
    protocol = _read_json(freeze)['protocol']
    lane = verified['lanes']['b']
    if lane['status'] != 'ready':
        return _blocked(*lane['reasons'])
    windows_path = _pinned(root, spec['windows'])
    events_path = _pinned(root, spec['events'])
    admission = _read_json(_pinned(root, spec['admission']))
    source = admission.get('source_identity')
    if (admission.get('schema_version') != 'detector-projection/development-v1'
            or any(admission.get(key) != spec[key] for key in ('dataset', 'clock', 'windows', 'events'))
            or not isinstance(source, dict) or admission.get('holdout_opened') is not False):
        raise ValueError('detector projection admission differs from the scored bytes')
    if dataset == 'alibaba-proxy':
        if source.get('data_manifest_sha256') != protocol['data']['manifest']['sha256']:
            raise ValueError('Alibaba proxy projection must use the frozen data manifest')
    elif (source.get('dataset_version') != 'alibaba-local-evaluation-admission/v1'
          or source.get('evaluation_ready') is not True or source.get('holdout_opened') is not False
          or not source.get('source_result_refs') or not source.get('oracle_certificate_refs')):
        raise ValueError('local detector projection requires a verified development archive')
    from sentinelops.ml.model_benchmark_detector_fit import FIT_PATH, verify_protocol_detector_fit
    from sentinelops.ml.model_benchmark_protocol import _ref

    if not (root / FIT_PATH).is_file():
        return _blocked('verified-detector-fit-required')
    fit = verify_protocol_detector_fit(root, expected_fit_sha256=spec['fit_sha256'],
        protocol_splits=_ref(root, protocol['data']['splits']),
        protocol_splits_sha256=protocol['data']['splits']['sha256'],
        protocol_source_manifest=_ref(root, protocol['code']['source_manifest']))
    if fit.provenance_sha256 not in {_BASELINE_FIT_SHA256, protocol['data']['manifest']['sha256']}:
        raise ValueError('detector fit provenance differs from the development protocol')
    if fit.status != 'ready':
        return _calibration_status(fit.status, fit.calibration)
    identity = {**spec, 'protocol_sha256': verified['protocol_sha256']}
    output = _within(root, f'report/b/{dataset}/{clock}')
    if (command in ('report', 'verify-report') or output.exists()) and not output.is_dir():
        return _blocked('detector-development-report-required')
    if (command != 'verify-report') and output.is_dir():
        return _verify_report(output, identity)

    import pyarrow.parquet as parquet
    from sentinelops.ml.model_benchmark_detector_evaluation import evaluate_detectors, score_detector_windows

    windows = parquet.read_table(windows_path).to_pylist()
    events = parquet.read_table(events_path).to_pylist()
    for row in chain(windows, events):
        if row['dataset'] != dataset:
            raise ValueError('Lane B projections must contain exactly one dataset')
        if dataset == 'local-development' and not re.fullmatch(r'ald-[0-9]+', row['group_id']):
            raise ValueError('archived local development group ID required')
        if dataset == 'alibaba-proxy' and split_of(row['service']) != 'evaluation':
            raise ValueError('Alibaba event and score ledgers require evaluation services')
    def evaluate(predictions):
        return evaluate_detectors(
            windows, events, predictions, clock=clock, thresholds={
                model: operating['threshold'] for model, operating in fit.calibration.items()
            }, policy_identity=fit.fit_sha256,
            persistence={'step_seconds': 60, 'entry_windows': fit.persistence, 'exit_windows': fit.persistence},
            event_kind='alibaba-proxy' if dataset == 'alibaba-proxy' else 'latency',
        )

    if command == 'verify-report':
        replayed = score_detector_windows(fit.model, windows, clock=clock)

        def verify_scores(saved):
            if len(saved) != len(replayed) or any(
                any(row.get(key) != value for key, value in expected.items() if key != 'inference_seconds')
                for row, expected in zip(saved, replayed)
            ):
                raise ValueError('Lane B report predictions differ from the pinned model and windows')
            return evaluate(saved)

        return _verify_report(output, identity, reevaluate=verify_scores)
    predictions = score_detector_windows(fit.model, windows, clock=clock, measure_inference=True)
    return _write_report(output, identity, evaluate(predictions))


def run_detector_stage(command, root, protocol_path, descriptor_path):
    """Consume an operator-selected development descriptor through the common CLI."""
    root = root.resolve()
    if not descriptor_path.is_file():
        return _blocked('lane-b-input-descriptor-required')
    spec = _read_json(descriptor_path)
    if command == 'fit':
        return _fit(root, protocol_path, spec)
    if command == 'calibrate':
        return _calibrate(root, spec)
    if command in ('score', 'evaluate', 'report', 'verify-report'):
        return _score(command, root, spec)
    raise ValueError('unsupported Lane B development stage')
