"""Fit the frozen lane B detectors and calibrate on disjoint Alibaba services."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import hashlib
from io import BytesIO
from itertools import groupby
import json
import math
from numbers import Real
import os
from pathlib import Path
import re
from statistics import median
from tempfile import NamedTemporaryFile
from time import perf_counter
from typing import TYPE_CHECKING

from sentinelops.ml.alibaba_training import MINUTE_MS, split_of
from sentinelops.ml.model_benchmark_calibration import calibrate
from sentinelops.ml.model_benchmark_detector_combo import COMBO_MODEL, calibrate_or_combo
from sentinelops.public_data.alibaba import AGGREGATE_VERSION, SOURCE
from sentinelops.telemetry.core import stable_id

if TYPE_CHECKING:
    from sentinelops.ml.model_benchmark_detector import DetectorConfig, DetectorRow, FrozenDetectors


BASELINE_LOOKBACK = 60
BASELINE_MINIMUM = 5
BUCKET_SECONDS = 60
PROXY_MULTIPLIER = 2.0
FIT_PATH = Path('fit/b/fit.json')
ALIBABA_SCOPE = 'verified-alibaba'
SYNTHETIC_SCOPE = 'synthetic-development'
_VERSION = 'benchmark-detector-fit/v1'
_TRANSFORM = 'causal-log1p-relative-latency/v1'
_SPLIT = 'sha256(msname)%10:0-2=reference,3-5=discovery,6-7=calibration,8-9=excluded'
_MODEL_IDS = ('adsketch', 'isolation_forest', 'median_mad')
_ROLE_NAMES = ('reference', 'discovery', 'calibration')
_FIT_CODE_PATHS = ('src/sentinelops/ml/model_benchmark_detector_fit.py',
                   'src/sentinelops/ml/model_benchmark_detector.py',
                   'src/sentinelops/ml/model_benchmark_calibration.py',
                   'src/sentinelops/ml/model_benchmark_detector_combo.py')


@dataclass(frozen=True)
class DetectorMinute:
    service: str
    timestamp: int
    latency_ms: float | None


@dataclass(frozen=True)
class VerifiedDetectorFit:
    model: FrozenDetectors
    calibration: dict
    persistence: int
    fit_sha256: str
    scope: str
    status: str
    provenance_sha256: str | None
    protocol_splits_sha256: str | None = None
    fit_code_sha256: dict | None = None


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _is_sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _ordered_minutes(minutes):
    ordered = []
    for minute in minutes:
        if (not isinstance(minute, DetectorMinute) or not isinstance(minute.service, str)
                or not minute.service.strip() or type(minute.timestamp) is not int
                or minute.timestamp < 0 or minute.timestamp % BUCKET_SECONDS):
            raise ValueError('service and minute-aligned epoch seconds required')
        value = minute.latency_ms
        if value is not None and (isinstance(value, bool) or not isinstance(value, Real)
                                  or not math.isfinite(value) or value < 0):
            raise ValueError('finite nonnegative latency or a missing minute required')
        ordered.append(DetectorMinute(minute.service, minute.timestamp, None if value is None else float(value)))
    ordered.sort(key=lambda minute: (minute.service, minute.timestamp))
    if any((left.service, left.timestamp) == (right.service, right.timestamp)
           for left, right in zip(ordered, ordered[1:])):
        raise ValueError('duplicate service minute')
    return ordered


def project_detector_run(minutes, *, run_id: str, split: str = 'scoring') -> tuple[DetectorRow, ...]:
    """Project raw bucket ends using only earlier contiguous minutes of the same service."""
    from sentinelops.ml.model_benchmark_detector import DetectorRow

    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError('run identity required')
    if split not in ('train', 'calibration', 'scoring', 'evaluation', 'inference'):
        raise ValueError('unknown detector split')
    projected = []
    for _, service_minutes in groupby(_ordered_minutes(minutes), key=lambda minute: minute.service):
        history = deque(maxlen=BASELINE_LOOKBACK)
        previous = None
        for minute in service_minutes:
            if previous is None or minute.timestamp - previous != BUCKET_SECONDS or minute.latency_ms is None:
                history.clear()
            value, label = None, None
            if minute.latency_ms is not None:
                if len(history) >= BASELINE_MINIMUM:
                    baseline = median(history)
                    value = math.log1p(minute.latency_ms) - math.log1p(baseline)
                    if split in ('train', 'calibration') and baseline > 0:
                        label = int(minute.latency_ms > PROXY_MULTIPLIER * baseline)
                history.append(minute.latency_ms)
            projected.append(DetectorRow(run_id, minute.service, split, minute.timestamp, value, label))
            previous = minute.timestamp
    return tuple(projected)


def _verified_aggregates(directory, provenance, expected_sha256):
    if not _is_sha(expected_sha256):
        raise ValueError('approved Alibaba provenance byte SHA256 required')
    content = Path(provenance).read_bytes()
    if _sha(content) != expected_sha256:
        raise ValueError('Alibaba provenance byte SHA256 mismatch')
    report = json.loads(content)
    if not isinstance(report, dict):
        raise ValueError('ADR-019 Alibaba provenance report required')
    config = report.get('config', {})
    if (report.get('report_version') != 'alibaba-relative-development/v2'
            or report.get('authorizing_adr') != 'ADR-019'
            or config.get('feature_set_version') != 'http-relative-5m/v2'
            or config.get('frozen_before_fit') is not True
            or config.get('splits', {}).get('assignment')
            != 'sha256(msname) mod 10: 0-5 train, 6-7 calibration, 8-9 evaluation'
            or report.get('config_sha256') != stable_id(config)):
        raise ValueError('ADR-019 Alibaba provenance and official service split required')
    archives = report.get('source_archives')
    if not isinstance(archives, list) or not archives:
        raise ValueError('Alibaba provenance has no source archives')
    by_archive = {}
    for entry in archives:
        if (not isinstance(entry, dict) or set(entry) != {'archive', 'archive_sha256', 'parquet_sha256'}
                or not isinstance(entry['archive'], str)
                or not re.fullmatch(r'MCRRTUpdate_[0-9]+\.tar\.gz', entry['archive'])
                or not _is_sha(entry['archive_sha256']) or not _is_sha(entry['parquet_sha256'])
                or entry['archive'] in by_archive):
            raise ValueError('unique checksummed Alibaba archives required')
        by_archive[entry['archive']] = entry
    directory = Path(directory)
    ordered_archives = sorted(by_archive.values(), key=lambda entry: int(
        entry['archive'].removeprefix('MCRRTUpdate_').removesuffix('.tar.gz')))

    def read_minutes():
        import pandas as pd

        frames = []
        for entry in ordered_archives:
            archive = entry['archive']
            stem = archive.removesuffix('.tar.gz')
            meta = json.loads((directory / (stem + '.json')).read_bytes())
            if (meta.get('source') != SOURCE or meta.get('aggregate_version') != AGGREGATE_VERSION
                    or any(meta.get(key) != value for key, value in entry.items())):
                raise ValueError('Alibaba aggregate provenance mismatch: ' + archive)
            content = (directory / (stem + '.parquet')).read_bytes()
            if _sha(content) != entry['parquet_sha256']:
                raise ValueError('Alibaba aggregate byte SHA256 mismatch: ' + archive)
            frame = pd.read_parquet(BytesIO(content), columns=['timestamp', 'msname', 'http_rt_ms'])
            if not pd.api.types.is_integer_dtype(frame['timestamp']) or frame['timestamp'].isna().any():
                raise ValueError('Alibaba aggregate timestamp must be integer epoch milliseconds')
            services = frame['msname'].unique()
            if any(not isinstance(service, str) or not service.strip() for service in services):
                raise ValueError('invalid Alibaba aggregate service')
            selected = frame[frame['msname'].isin([service for service in services
                                                   if split_of(service) != 'evaluation'])]
            if selected.empty:
                continue
            if (selected['timestamp'] < 0).any():
                raise ValueError('invalid Alibaba aggregate timestamp')
            latency = selected['http_rt_ms'].dropna()
            if not latency.empty and (not pd.api.types.is_numeric_dtype(latency)
                                      or pd.api.types.is_bool_dtype(latency)
                                      or not latency.between(0, math.inf, inclusive='left').all()):
                raise ValueError('invalid Alibaba aggregate latency')
            if latency.empty and not pd.api.types.is_numeric_dtype(selected['http_rt_ms']):
                selected = selected.assign(http_rt_ms=selected['http_rt_ms'].astype(float))
            frames.append(selected)
        if not frames:
            return
        combined = pd.concat(frames, ignore_index=True)
        collapsed = combined.groupby(['timestamp', 'msname'], as_index=False).agg(http_rt_ms=('http_rt_ms', 'mean'))
        for timestamp, service, latency in collapsed.itertuples(index=False, name=None):
            yield DetectorMinute(service, timestamp // MINUTE_MS * BUCKET_SECONDS,
                                 None if pd.isna(latency) else latency)

    return {'scope': ALIBABA_SCOPE, 'source': SOURCE, 'provenance_sha256': expected_sha256,
            'archive_count': len(ordered_archives), 'archives_sha256': stable_id(ordered_archives)}, read_minutes()


def _segments(minutes):
    for _, service_minutes in groupby(minutes, key=lambda minute: minute.service):
        segment = []
        for minute in service_minutes:
            if segment and (minute.latency_ms is None or minute.timestamp - segment[-1].timestamp != BUCKET_SECONDS):
                yield segment
                segment = []
            if minute.latency_ms is not None:
                segment.append(minute)
        if segment:
            yield segment


def _protocol_cohort_constraints(records):
    """Bind training partitions and calibration anchors to admitted protocol records."""
    constraints = {}
    for row in records:
        role = row['role']
        if role == 'development':
            continue
        service = row['service']
        start, end, anchor = (row[key] for key in ('partition_start', 'partition_end', 'anchor_minute'))
        if (role not in ('train', 'calibration') or row['parent_split'] != role
                or split_of(service) != role or any(type(value) is not int for value in (start, end, anchor))
                or not 0 <= start <= anchor <= end):
            raise ValueError('invalid protocol detector partition')
        entry = constraints.setdefault(service, {'role': role, 'start': start, 'end': end, 'anchors': set()})
        if (entry['role'], entry['start'], entry['end']) != (role, start, end):
            raise ValueError('conflicting protocol detector partitions')
        if anchor in entry['anchors']:
            raise ValueError('duplicate protocol detector anchor')
        entry['anchors'].add(anchor)
    if {item['role'] for item in constraints.values()} != {'train', 'calibration'}:
        raise ValueError('protocol detector requires train and calibration records')
    return constraints


def _cohorts(minutes, source, window_length, *, constraints=None):
    selected = (minute for minute in minutes if not isinstance(minute, DetectorMinute)
                or not isinstance(minute.service, str) or (split_of(minute.service) != 'evaluation'
                and (constraints is None or (minute.service in constraints
                    and constraints[minute.service]['start'] <= minute.timestamp // BUCKET_SECONDS
                    <= (max(constraints[minute.service]['anchors']) if constraints[minute.service]['role'] == 'calibration'
                        else constraints[minute.service]['end'])))))
    rows = {role: [] for role in _ROLE_NAMES}
    segments = {role: [] for role in _ROLE_NAMES}
    anchors = {}
    covered = set()
    calibration_rows = {}
    for segment in _segments(_ordered_minutes(selected)):
        first = segment[0]
        split = split_of(first.service)
        role = split
        if split == 'train':
            bucket = int(hashlib.sha256(first.service.encode()).hexdigest(), 16) % 10
            role = 'reference' if bucket <= 2 else 'discovery'
        run_id = stable_id({'scope': source['scope'], 'service': first.service, 'start': first.timestamp})
        projected = project_detector_run(segment, run_id=run_id, split=split)
        segments[role].append({'run_id': run_id, 'service': first.service, 'split': split,
                               'start': first.timestamp, 'end': segment[-1].timestamp,
                               'rows': len(segment), 'sha256': stable_id([asdict(row) for row in projected])})
        if role != 'calibration':
            rows[role].extend(projected)
            continue
        stride = BASELINE_LOOKBACK + window_length if constraints is None else 1
        for index in range(BASELINE_MINIMUM + window_length - 1, len(projected), stride):
            minute = projected[index].timestamp // BUCKET_SECONDS
            if constraints is not None and minute not in constraints[first.service]['anchors']:
                continue
            window = projected[index - window_length + 1:index + 1]
            labels = {row.label for row in window}
            label = 1 if 1 in labels else None if None in labels else 0
            anchors[(run_id, first.service, window[-1].timestamp)] = label
            covered.add((first.service, minute))
            for row in window:
                calibration_rows[(row.run_id, row.service, row.timestamp)] = row
    if constraints is not None:
        expected = {(service, anchor) for service, entry in constraints.items()
                    if entry['role'] == 'calibration' for anchor in entry['anchors']}
        for service, minute in sorted(expected - covered):
            run_id = stable_id({'scope': source['scope'], 'service': service, 'missing_anchor': minute})
            anchors[(run_id, service, minute * BUCKET_SECONDS)] = None
    rows['calibration'] = list(calibration_rows.values())
    summary = {role: {'segments': len(items), 'services': len({item['service'] for item in items}),
                      'minutes': sum(item['rows'] for item in items), 'sha256': stable_id(items)}
               for role, items in segments.items()}
    return rows, anchors, summary


def _calibration_ledger(model, rows, anchors, *, include_sources=False):
    from sentinelops.ml.model_benchmark_detector import predict_detectors

    ledger = {name: [] for name in _MODEL_IDS}
    seen = set()
    for prediction in predict_detectors(model, rows):
        key = (prediction.run_id, prediction.service, prediction.timestamp)
        if key not in anchors:
            continue
        model_id = prediction.model_id
        if model_id not in ledger or (model_id, key) in seen:
            raise ValueError('unexpected or duplicate calibration prediction')
        seen.add((model_id, key))
        run_id, service, timestamp = key
        row = {'record_id': stable_id({'run_id': run_id, 'service': service, 'timestamp': timestamp}),
               'eligible': True, 'risk_label': anchors[key],
               'score': prediction.score, 'missing_reason': prediction.reason}
        if include_sources:
            row.update(run_id=run_id, service=service, timestamp=timestamp)
        ledger[model_id].append(row)
    if not include_sources and len(seen) != len(anchors) * len(_MODEL_IDS):
        raise ValueError('incomplete calibration prediction timeline')
    for key, label in anchors.items():
        for model_id in _MODEL_IDS:
            if (model_id, key) not in seen:
                run_id, service, timestamp = key
                row = {'record_id': stable_id({'run_id': run_id, 'service': service, 'timestamp': timestamp}),
                       'eligible': True, 'risk_label': label, 'score': None,
                       'missing_reason': 'missing-contiguous-detector-history'}
                if include_sources:
                    row.update(run_id=run_id, service=service, timestamp=timestamp)
                ledger[model_id].append(row)
    return {name: sorted(items, key=lambda item: item['record_id']) for name, items in ledger.items()}


def _operating_points(ledger, identity, model_sha256):
    if set(ledger) != set(_MODEL_IDS):
        raise ValueError('calibration must cover all three detectors')
    timelines = [[(row['record_id'], row['eligible'], row['risk_label']) for row in ledger[name]]
                 for name in _MODEL_IDS]
    if any(timeline != timelines[0] for timeline in timelines[1:]):
        raise ValueError('calibration models must share a timeline and labels')
    points = {name: calibrate(ledger[name], calibration_identity=stable_id(
        {'fit_input_sha256': identity, 'model_sha256': model_sha256, 'model_id': name})) for name in _MODEL_IDS}
    combo = calibrate_or_combo(ledger)
    points[COMBO_MODEL] = {**combo, 'comparison': '>='}
    return points


def _status(points):
    return 'ready' if all(point['status'] == 'ready' for point in points.values()) else 'not-ready'


def _verify_replay(original, replay):
    max_delta = 0.0
    score_count = 0
    for name in _MODEL_IDS:
        if len(original[name]) != len(replay[name]):
            raise ValueError('restored detector prediction count changed')
        for before, after in zip(original[name], replay[name]):
            before_score, after_score = before['score'], after['score']
            if ({key: value for key, value in before.items() if key != 'score'}
                    != {key: value for key, value in after.items() if key != 'score'}
                    or (before_score is None) != (after_score is None)):
                raise ValueError('restored detector prediction identity or availability changed')
            if before_score is not None:
                if not math.isfinite(before_score) or not math.isfinite(after_score):
                    raise ValueError('checkpoint replay requires finite scores')
                max_delta = max(max_delta, abs(before_score - after_score))
                score_count += 1
    if max_delta > 1e-6:
        raise ValueError('restored detector scores changed beyond tolerance')
    return {'method': 'completed-model-json-round-trip', 'scored_predictions': score_count,
            'max_abs_delta': max_delta, 'tolerance': 1e-6}


def _write_checkpoint(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, prefix='.detector-fit-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _fit(root, minutes, source, config, persistence, *, constraints=None):
    from sentinelops.ml.model_benchmark_detector import FrozenDetectors, fit_detectors

    if config.bucket_seconds != BUCKET_SECONDS or config.seed != 42:
        raise ValueError('benchmark detector requires minute buckets and seed 42')
    if type(persistence) is not int or persistence < 1:
        raise ValueError('positive detector persistence required')
    rows, anchors, summary = _cohorts(minutes, source, config.window_length, constraints=constraints)
    identity = {'source': source, 'split_assignment': _SPLIT, 'transform': _TRANSFORM,
                'config': asdict(config), 'persistence': persistence, 'cohorts': summary}
    identity_sha256 = stable_id(identity)
    path = Path(root) / FIT_PATH
    if not path.is_file():
        started = perf_counter()
        model = fit_detectors(rows['reference'], config, discovery_rows=rows['discovery'])
        seconds = perf_counter() - started
        payload = model.to_dict()
        restored = FrozenDetectors.from_dict(json.loads(_encode(payload)))
        if restored.digest != model.digest:
            raise ValueError('restored detector model IDs changed')
        ledger = _calibration_ledger(model, rows['calibration'], anchors, include_sources=constraints is not None)
        replay = _calibration_ledger(restored, rows['calibration'], anchors, include_sources=constraints is not None)
        recovery = _verify_replay(ledger, replay)
        points = _operating_points(ledger, identity_sha256, model.digest)
        restored_points = _operating_points(replay, identity_sha256, model.digest)
        if (any((points[name]['status'], points[name]['threshold'])
                != (restored_points[name]['status'], restored_points[name]['threshold']) for name in _MODEL_IDS)
                or points[COMBO_MODEL] != restored_points[COMBO_MODEL]):
            raise ValueError('restored detector operating points changed')
        artifact = {'schema_version': _VERSION, 'identity': identity, 'input_sha256': identity_sha256,
                    'model': payload, 'model_sha256': model.digest, 'fit_seconds': seconds,
                    'calibration_rows': ledger, 'calibration': points, 'recovery': recovery,
                    'status': _status(points), 'activation_allowed': False}
        try:
            _write_checkpoint(path, _encode(artifact))
        except FileExistsError:
            pass
    content = path.read_bytes()
    result, artifact = _verify(content, _sha(content), allow_synthetic=source['scope'] == SYNTHETIC_SCOPE)
    if artifact['input_sha256'] != identity_sha256:
        raise ValueError('a different detector fit is already present')
    if _calibration_ledger(result.model, rows['calibration'], anchors,
                           include_sources=constraints is not None) != artifact['calibration_rows']:
        raise ValueError('saved detector does not reproduce its bound calibration timeline')
    return {'fit_sha256': result.fit_sha256, 'model_sha256': result.model.digest, 'scope': result.scope,
            'status': result.status, 'calibration': result.calibration, 'fit_seconds': artifact['fit_seconds'],
            'cohorts': summary, 'recovery': artifact['recovery'], 'persistence': result.persistence,
            'provenance_sha256': result.provenance_sha256, 'activation_allowed': False}


def fit_detector_benchmark(root, *, aggregate_dir, provenance, provenance_sha256,
                           config: DetectorConfig, persistence: int = 2, protocol_splits=None,
                           protocol_splits_sha256=None):
    """Fit from an operator-approved ADR-019 report pin and its exact aggregate shards."""
    source, minutes = _verified_aggregates(aggregate_dir, provenance, provenance_sha256)
    if (protocol_splits is None) != (protocol_splits_sha256 is None):
        raise ValueError('protocol records and split SHA256 must be supplied together')
    constraints = None
    if protocol_splits is not None:
        if not _is_sha(protocol_splits_sha256) or _sha(protocol_splits) != protocol_splits_sha256:
            raise ValueError('protocol split SHA256 mismatch')
        splits = json.loads(protocol_splits)
        if not isinstance(splits, dict) or set(splits) != {'records'} or not isinstance(splits['records'], list):
            raise ValueError('protocol split records required')
        constraints = _protocol_cohort_constraints(splits['records'])
        source['protocol_splits_sha256'] = protocol_splits_sha256
        project = Path(__file__).resolve().parents[3]
        source['fit_code_sha256'] = {name: _sha((project / name).read_bytes()) for name in _FIT_CODE_PATHS}
    return _fit(root, minutes, source, config, persistence, constraints=constraints)


def fit_synthetic_detector_benchmark(root, *, minutes, config: DetectorConfig, persistence: int = 2):
    """Exercise the fit path on explicitly synthetic development minutes."""
    return _fit(root, minutes, {'scope': SYNTHETIC_SCOPE, 'source': 'synthetic'}, config, persistence)


def _verify(content, expected_fit_sha256, *, allow_synthetic):
    from sentinelops.ml.model_benchmark_detector import FrozenDetectors

    if not _is_sha(expected_fit_sha256) or _sha(content) != expected_fit_sha256:
        raise ValueError('detector fit byte SHA256 mismatch')
    artifact = json.loads(content)
    if (content != _encode(artifact) or artifact['schema_version'] != _VERSION
            or artifact['activation_allowed'] is not False):
        raise ValueError('invalid frozen detector fit artifact')
    identity = artifact['identity']
    source = identity['source']
    scope = source['scope']
    if scope == SYNTHETIC_SCOPE:
        if allow_synthetic is not True or source != {'scope': SYNTHETIC_SCOPE, 'source': 'synthetic'}:
            raise ValueError('synthetic detector artifacts require explicit development verification')
    elif (scope != ALIBABA_SCOPE or source['source'] != SOURCE
          or not _is_sha(source.get('provenance_sha256')) or not _is_sha(source.get('archives_sha256'))
          or type(source.get('archive_count')) is not int or source['archive_count'] < 1):
        raise ValueError('verified Alibaba source required')
    if 'protocol_splits_sha256' in source and not _is_sha(source['protocol_splits_sha256']):
        raise ValueError('invalid detector protocol split SHA256')
    if 'fit_code_sha256' in source and (not isinstance(source['fit_code_sha256'], dict)
            or set(source['fit_code_sha256']) != set(_FIT_CODE_PATHS)
            or not all(_is_sha(value) for value in source['fit_code_sha256'].values())):
        raise ValueError('invalid detector fit code SHA256')
    persistence = identity['persistence']
    if (stable_id(identity) != artifact['input_sha256'] or identity['split_assignment'] != _SPLIT
            or identity['transform'] != _TRANSFORM or type(persistence) is not int or persistence < 1):
        raise ValueError('detector fit input identity mismatch')
    model = FrozenDetectors.from_dict(artifact['model'])
    if (model.digest != artifact['model_sha256'] or asdict(model.config) != identity['config']
            or model.config.bucket_seconds != BUCKET_SECONDS or model.config.seed != 42):
        raise ValueError('detector model identity mismatch')
    points = _operating_points(artifact['calibration_rows'], artifact['input_sha256'], model.digest)
    recovery = artifact['recovery']
    scored = sum(row['score'] is not None for items in artifact['calibration_rows'].values() for row in items)
    if (points != artifact['calibration'] or _status(points) != artifact['status']
            or recovery['method'] != 'completed-model-json-round-trip' or recovery['scored_predictions'] != scored
            or recovery['tolerance'] != 1e-6 or not 0 <= recovery['max_abs_delta'] <= 1e-6
            or not math.isfinite(artifact['fit_seconds']) or artifact['fit_seconds'] < 0):
        raise ValueError('detector calibration or recovery receipt mismatch')
    result = VerifiedDetectorFit(model, points, persistence, expected_fit_sha256, scope, artifact['status'],
                                 source.get('provenance_sha256'), source.get('protocol_splits_sha256'),
                                 source.get('fit_code_sha256'))
    return result, artifact


def verify_detector_fit(root, *, expected_fit_sha256, allow_synthetic=False) -> VerifiedDetectorFit:
    """Verify the complete checkpoint by a trusted pin and rebuild its operating points."""
    try:
        content = (Path(root) / FIT_PATH).read_bytes()
        result, _ = _verify(content, expected_fit_sha256, allow_synthetic=allow_synthetic)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('malformed detector artifact') from exc
    return result


def verify_protocol_detector_fit(root, *, expected_fit_sha256, protocol_splits, protocol_splits_sha256,
                                 protocol_source_manifest=None):
    """Verify the checkpoint's training segments and calibration timeline against pinned splits."""
    if not _is_sha(protocol_splits_sha256) or _sha(protocol_splits) != protocol_splits_sha256:
        raise ValueError('protocol split SHA256 mismatch')
    result, artifact = _verify((Path(root) / FIT_PATH).read_bytes(), expected_fit_sha256, allow_synthetic=False)
    if result.protocol_splits_sha256 != protocol_splits_sha256:
        raise ValueError('detector fit split differs from the development protocol')
    if protocol_source_manifest is not None:
        manifest = json.loads(protocol_source_manifest)
        refs = manifest['files']
        if not isinstance(result.fit_code_sha256, dict):
            raise ValueError('detector fit lacks pinned source code')
        for name, digest in result.fit_code_sha256.items():
            matches = [ref for ref in refs if ref['path'].endswith('/source/' + name)]
            if len(matches) != 1 or matches[0]['sha256'] != digest:
                raise ValueError('detector fit code differs from the development protocol')
    splits = json.loads(protocol_splits)
    if not isinstance(splits, dict) or set(splits) != {'records'} or not isinstance(splits['records'], list):
        raise ValueError('protocol split records required')
    constraints = _protocol_cohort_constraints(splits['records'])
    for segment in result.model.training_segments:
        entry = constraints.get(segment.service)
        if (not entry or entry['role'] != 'train' or segment.split != 'train'
                or not entry['start'] * BUCKET_SECONDS <= segment.start_timestamp
                <= segment.end_timestamp <= entry['end'] * BUCKET_SECONDS):
            raise ValueError('detector training segment crosses the protocol partition')
    expected = {(service, minute * BUCKET_SECONDS) for service, entry in constraints.items()
                if entry['role'] == 'calibration' for minute in entry['anchors']}
    for ledger in artifact['calibration_rows'].values():
        actual = set()
        for row in ledger:
            service, timestamp, run_id = row.get('service'), row.get('timestamp'), row.get('run_id')
            key = (service, timestamp)
            if (not isinstance(service, str) or type(timestamp) is not int or not isinstance(run_id, str)
                    or row['record_id'] != stable_id({'run_id': run_id, 'service': service, 'timestamp': timestamp})
                    or key in actual):
                raise ValueError('detector calibration source identity differs')
            actual.add(key)
        if actual != expected:
            raise ValueError('detector calibration anchors differ from the protocol')
    return result
