"""Project the verified ADR-016 local development epoch for detector replay."""

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from sentinelops.contracts import utc_time
from sentinelops.experiments.local_corpus import ALIBABA_PLAN, acquisition_lock, load_plan
from sentinelops.incidents.health import validate_policy
from sentinelops.ml.alibaba_local_evaluation import metric_event_cases
from sentinelops.ml.local_data import PROJECT, _inside, audit_alibaba_local
from sentinelops.ml.model_benchmark_detector_stage import _json_bytes, _sha, _within
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.prometheus import mean_latency_ms
from sentinelops.telemetry.store import ArtifactStore


def _project_case(store, case, policy):
    if case['role'] != 'development-validation':
        raise ValueError('detector archive projection requires development-validation')
    result = store.read(case['source_result_ref'])
    captured = _inside(store.root, result['run_directory'])
    run = json.loads((captured / 'run.json').read_bytes())
    if stable_id(run) != result['run_sha256']:
        raise ValueError('detector archive run changed after admission')
    warmup_end = utc_time(run['warmup_end_at'])
    serving = ArtifactStore(captured / 'serving')
    source = serving.read(run['source_manifest_ref'])
    normalized = serving.read(source['normalization_manifest_ref'])
    observations = {}
    for ref in normalized['observation_refs']:
        observation = serving.read(ref)
        if not observation['quality']['eligible_for_inference']:
            continue
        end = int(utc_time(observation['bucket_end']).timestamp())
        key = observation['service'], end
        if key in observations:
            raise ValueError('duplicate eligible source bucket in detector archive')
        raw_ref, = observation['provenance']['source_refs']
        value = mean_latency_ms(serving.read(raw_ref))
        observations[key] = (value, observation['available_at'], raw_ref)

    windows, events = [], []
    for service, series in sorted(case['oracle']['services'].items()):
        identity = {'dataset': 'local-development', 'group_id': case['group_id'],
                    'run_id': case['run_id'], 'service': service}
        for measure, threshold, kind in (('p95_latency_ms', policy['latency_ms'], 'latency'),
                                         ('error_rate', policy['error_rate'], '5xx')):
            projected, = metric_event_cases([case], service, measure, threshold, policy['entry_buckets'])
            evidence = projected['oracle']['services'][service]
            onset = evidence['first_violation_at']
            if onset is not None:
                eligible = utc_time(onset) >= warmup_end
                events.append({**identity, 'event_id': stable_id({**identity, 'kind': kind, 'onset_at': onset}),
                               'onset_at': onset, 'confirmed_at': evidence['confirmed_at'],
                               'kind': kind, 'eligible': eligible,
                               'reason': None if eligible else 'onset-during-warmup',
                               'oracle_ref': case['oracle_ref']})
        for bucket, item in sorted(series['buckets'].items(), key=lambda pair: int(pair[0])):
            end = int(bucket) + 60
            end_at = datetime.fromtimestamp(end, timezone.utc)
            eligible = end_at > warmup_end
            value, available_at, raw_ref = observations.get((service, end), (None, None, None))
            latency = item['p95_latency_ms'] if item['availability'] == 'VERIFIED' else None
            label = int(latency > policy['latency_ms']) if latency is not None else None
            windows.append({**identity, 'bucket_end': timestamp(end_at), 'available_at': available_at,
                            'decision_at': timestamp(end_at), 'eligible': eligible,
                            'reason': None if eligible else 'archive-warmup', 'y': label,
                            'label_reason': None if label is not None else 'latency-oracle-unavailable',
                            'healthy': case['kind'] == 'healthy' and label == 0 and item['breach'] is False,
                            'value': value, 'raw_ref': raw_ref, 'oracle_ref': case['oracle_ref']})
    return windows, events


def prepare_detector_archive(root, archive, fit_sha256):
    """Publish a score descriptor and its pinned source/label Parquet files."""
    import pyarrow as arrow
    import pyarrow.parquet as parquet
    from sentinelops.ml.model_benchmark_detector_fit import FIT_PATH, verify_detector_fit

    root = Path(root).resolve()
    store = ArtifactStore(archive)
    if root == store.root or root.is_relative_to(store.root):
        raise ValueError('separate benchmark output and materialized development archive required')
    if not store.root.is_dir():
        return {'status': 'blocked', 'reasons': ['materialized-development-archive-required']}
    if not (root / FIT_PATH).is_file():
        return {'status': 'blocked', 'reasons': ['verified-detector-fit-required']}
    fit = verify_detector_fit(root, expected_fit_sha256=fit_sha256)
    if fit.status != 'ready':
        return {'status': 'blocked', 'reasons': ['completed-detector-calibration-required']}
    plan = load_plan(PROJECT / 'configs' / ALIBABA_PLAN.name)
    policy = json.loads((PROJECT / 'configs/slo_health_policy.json').read_bytes())
    validate_policy(policy)
    with acquisition_lock(store):
        admission, _, cases = audit_alibaba_local(store, plan)
        if admission['evaluation_ready'] is not True:
            return {'status': 'blocked', 'reasons': ['development-archive-incomplete'],
                    'admission': admission}
        windows, events = [], []
        for case in cases:
            case_windows, case_events = _project_case(store, case, policy)
            windows.extend(case_windows)
            events.extend(case_events)
    directory = _within(root, 'input/detector-local-replay')
    directory.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.detector-archive-', dir=directory.parent) as scratch:
        staged = Path(scratch) / 'projection'
        staged.mkdir()
        descriptor = {'schema_version': 'detector-score-input/development-v1',
                      'dataset': 'local-development', 'clock': 'replay', 'fit_sha256': fit_sha256}
        for name, rows in (('windows', windows), ('events', events)):
            path = staged / (name + '.parquet')
            parquet.write_table(arrow.Table.from_pylist(rows), path)
            descriptor[name] = {'path': str((directory / path.name).relative_to(root)), 'sha256': _sha(path)}
        admission_path = staged / 'admission.json'
        admission_path.write_bytes(_json_bytes({
            'schema_version': 'detector-projection/development-v1',
            'dataset': descriptor['dataset'], 'clock': descriptor['clock'],
            'windows': descriptor['windows'], 'events': descriptor['events'],
            'source_identity': {key: value for key, value in admission.items() if key != 'created_at'},
            'holdout_opened': False,
        }))
        descriptor['admission'] = {'path': str((directory / admission_path.name).relative_to(root)),
                                   'sha256': _sha(admission_path)}
        encoded = _json_bytes(descriptor)
        (staged / 'score.json').write_bytes(encoded)
        try:
            staged.rename(directory)
        except OSError:
            if not directory.is_dir() or (directory / 'score.json').read_bytes() != encoded:
                raise
    for name in ('windows', 'events', 'admission'):
        if _sha(_within(root, descriptor[name]['path'])) != descriptor[name]['sha256']:
            raise ValueError('existing development archive projection changed')
    return {'status': 'ready', 'scope': 'development', 'clock': 'replay',
            'descriptor': str(directory / 'score.json'), 'windows': len(windows), 'events': len(events)}
