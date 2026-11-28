"""Write Alibaba proxy events and a receipt from pinned development windows.

The pure projector requires ``relative_minute`` on every window. An explicit
``--minute-zero`` supplies missing coordinates; input rows never set the epoch.
"""

import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from sentinelops.contracts import utc_time
from sentinelops.ml.alibaba_training import split_of
from sentinelops.ml.model_benchmark_alibaba_events import (
    ALIBABA_EVENT_RULE, ALIBABA_EVENT_RULE_ID, project_alibaba_proxy_events, proxy_minute_labels,
)
from sentinelops.ml.model_benchmark_detector_evaluation import KEY_FIELDS
from sentinelops.ml.model_benchmark_detector_fit import DetectorMinute
from sentinelops.ml.model_benchmark_detector_stage import _json_bytes, _read_json, _require_fields, _sha, _within
from sentinelops.telemetry.core import timestamp


def run(root, *, descriptor, output, minute_zero=None):
    import pyarrow as arrow
    import pyarrow.parquet as parquet

    root, output = Path(root).resolve(), Path(output).resolve()
    spec = _read_json(Path(descriptor))
    _require_fields(spec, 'schema_version dataset clock fit_sha256 windows events admission')
    if (spec['schema_version'] != 'detector-score-input/development-v1'
            or spec['dataset'] != 'alibaba-proxy' or spec['clock'] not in ('replay', 'arrival-aware')):
        raise ValueError('Alibaba proxy development score descriptor required')
    ref = spec['windows']
    _require_fields(ref, 'path sha256')
    content = _within(root, ref['path']).read_bytes()
    input_sha = hashlib.sha256(content).hexdigest()
    if input_sha != ref['sha256']:
        raise ValueError('Alibaba detector windows SHA-256 differs from descriptor')
    windows = parquet.read_table(BytesIO(content)).to_pylist()
    if not windows:
        raise ValueError('Alibaba event projection requires scoring windows')
    origin = utc_time(minute_zero) if minute_zero is not None else None
    minutes = []
    for row in windows:
        service = row['service']
        if not isinstance(service, str) or not service.strip() or split_of(service) != 'evaluation':
            raise ValueError('Alibaba event projection requires evaluation services')
        if origin is not None:
            seconds = (utc_time(row['bucket_end']) - origin).total_seconds()
            step = ALIBABA_EVENT_RULE['bucket_seconds']
            if seconds % step or ('relative_minute' in row and row['relative_minute'] != seconds // step):
                raise ValueError('bucket end or relative minute disagrees with minute zero')
            row.setdefault('relative_minute', int(seconds // step))
        if 'relative_minute' not in row:
            raise ValueError('relative_minute or --minute-zero required to verify the D1 scoring interval')
        minute = row['relative_minute']
        if type(minute) is not int or not ALIBABA_EVENT_RULE['warmup_start_minute'] <= minute <= 352:
            raise ValueError('Alibaba input relative minute must be in 259–352 including causal warmup')
        if type(row['eligible']) is not bool or minute < 324 and row['eligible']:
            raise ValueError('Alibaba warmup windows must be ineligible')
        label = row['y']
        if label is not None and (type(label) is not int or label not in (0, 1)):
            raise ValueError('Alibaba event label must be 0, 1 or null')
        end = utc_time(row['bucket_end'])
        if end.second or end.microsecond:
            raise ValueError('Alibaba event bucket must close on a UTC minute')
        if 'value' not in row:
            raise ValueError('raw latency-ms value required to reproduce the per-minute oracle')
        minutes.append(DetectorMinute(service, int(end.timestamp()), row['value']))
    labels = {(row['service'], row['timestamp']): row['y'] for row in proxy_minute_labels(minutes)}
    for row in windows:
        row['y'] = labels[row['service'], int(utc_time(row['bucket_end']).timestamp())]
        row['label_reason'] = 'insufficient-contiguous-or-positive-baseline' if row['y'] is None else None
        if row['relative_minute'] < 324:
            row['eligibility_reason'] = 'causal-warmup'
    events, healthy_keys = project_alibaba_proxy_events(windows)
    for row in windows:
        row['bucket_end'] = timestamp(utc_time(row['bucket_end']))
        row['healthy'] = tuple(row[field] for field in KEY_FIELDS) in healthy_keys
    windows.sort(key=lambda row: tuple(row[field] for field in KEY_FIELDS))
    windows = [dict(sorted(row.items())) for row in windows]
    schema = arrow.schema([(field, arrow.string()) for field in (
        'event_id', 'dataset', 'group_id', 'run_id', 'service', 'kind', 'onset_at', 'confirmed_at', 'reason', 'end_at',
        'censor_until',
    )] + [('eligible', arrow.bool_()), ('length_minutes', arrow.int64()), ('positive_minutes', arrow.int64())])
    eligible = sum(event['eligible'] for event in events)
    ineligible_by_reason = Counter(event['reason'] for event in events if not event['eligible'])
    by_service = Counter(event['service'] for event in events)
    single_minute = sum(event['length_minutes'] == 1 for event in events)

    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.alibaba-events-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'projection'
        stage.mkdir()
        events_path = stage / 'events.parquet'
        parquet.write_table(arrow.Table.from_pylist(events, schema=schema), events_path)
        windows_path = stage / 'windows.parquet'
        parquet.write_table(arrow.Table.from_pylist(windows), windows_path)
        receipt = {
            'schema_version': 'alibaba-detector-event-projection/v1',
            'rule_id': ALIBABA_EVENT_RULE_ID,
            'windows': ref,
            'projected_windows': {'path': 'windows.parquet', 'sha256': _sha(windows_path)},
            'events': {'path': 'events.parquet', 'sha256': _sha(events_path)},
            'eligible_events': eligible,
            'ineligible_events': len(events) - eligible,
            'ineligible_by_reason': dict(ineligible_by_reason),
            'services_with_events': len(by_service),
            'events_by_service': dict(by_service),
            'event_lengths': {'one_minute': single_minute, 'two_or_more_minutes': len(events) - single_minute},
            'healthy_windows': len(healthy_keys),
        }
        if minute_zero is not None:
            receipt['minute_zero'] = minute_zero
        (stage / 'receipt.json').write_bytes(_json_bytes(receipt))
        stage.rename(output)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--descriptor', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--minute-zero', help='UTC timestamp for relative minute zero if absent from the windows.')
    args = parser.parse_args()
    print(json.dumps(run(args.root, descriptor=args.descriptor, output=args.output, minute_zero=args.minute_zero),
                     sort_keys=True))


if __name__ == '__main__':
    main()
