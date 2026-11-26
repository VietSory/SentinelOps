"""Verify both 400 ms development pilots without opening holdout."""
import argparse
import json
import math
from pathlib import Path

import sentinelops

from sentinelops.contracts import utc_time
from sentinelops.experiments.local_corpus import clean_source, load_plan, read_binding, registration_path
from sentinelops.ml.alibaba_local_evaluation import latency_event_intervals
from sentinelops.ml.local_data import verify_capture
from sentinelops.ml.model_benchmark_protocol import publish_freeze
from sentinelops.telemetry.core import BUSINESS_ROUTES
from sentinelops.telemetry.store import ArtifactStore


def assess(oracle, start, stop):
    """Check every active minute and count independent p95 latency episodes."""
    start, stop = utc_time(start), utc_time(stop)
    if stop <= start:
        raise ValueError('pilot active interval must be positive')
    first = int(start.timestamp()) // 60 * 60
    last = math.ceil(stop.timestamp() / 60) * 60
    result = {}
    for service in BUSINESS_ROUTES:
        buckets = oracle['services'][service]['buckets']
        values, missing = [], 0
        for minute in range(first, last, 60):
            row = buckets.get(str(minute))
            value = row.get('p95_latency_ms') if row else None
            if (not row or row.get('availability') != 'VERIFIED' or type(value) not in (int, float)
                    or not math.isfinite(value) or value < 0):
                missing += 1
            else:
                values.append(value)
        intervals = latency_event_intervals(oracle, service, threshold=750, entry_buckets=2, recovery_buckets=3)
        events = [event for event in intervals if utc_time(event['onset_at']) < stop
                  and (event['recovered_at'] is None or utc_time(event['recovered_at']) > start)]
        result[service] = {'verified_minutes': len(values), 'unknown_minutes': missing,
                           'maximum_p95_ms': max(values) if values else None,
                           'latency_episodes': len(events)}
    return result


def run(root, *, ordinal, project, output):
    root, project, output = Path(root).resolve(), Path(project).resolve(), Path(output).resolve()
    if not output.is_relative_to(root):
        raise ValueError('pilot receipt must remain inside its source archive')
    checker = Path(__file__).resolve().parents[1]
    if not Path(sentinelops.__file__).samefile(checker / 'src/sentinelops/__init__.py'):
        raise ValueError('pilot verifier must import its checkout source')
    commit = clean_source(checker)
    plan = load_plan(project / 'configs/alibaba_low_ramp_development.json')
    store = ArtifactStore(root)
    group = f'ale-{ordinal:02d}'
    _, session = read_binding(store, registration_path(store, group))
    cases = {}
    for kind in ('payment-gradual', 'inventory-gradual'):
        verified = verify_capture(store, plan, session, kind, project=project)
        result = store.read(verified['source_result_ref'])
        run = json.loads((root / result['run_directory'] / 'run.json').read_bytes())
        if not verified['gradual_control_verified'] or run['parameters']['maximum_delay_ms'] != 400:
            raise ValueError('pilot requires the registered 400 ms gradual control')
        cases[kind] = {'run_id': verified['run_id'], 'source_result_ref': verified['source_result_ref'],
                       'oracle_ref': verified['oracle_ref'], 'control_steps': verified['observed_gradual_control_steps'],
                       'services': assess(verified['oracle'], run['fault_start_at'], run['recovery_start_at'])}
    if clean_source(checker) != commit:
        raise ValueError('pilot verifier source changed during replay')
    ready = all(not item['unknown_minutes'] and not item['latency_episodes']
                for case in cases.values() for item in case['services'].values())
    receipt = {'schema_version': 'benchmark-low-ramp-pilot/development-v1',
               'status': 'ready' if ready else 'blocked', 'group_id': group, 'checker_source_commit': commit,
               'maximum_delay_ms': 400, 'cases': cases, 'holdout_opened': False, 'activation_allowed': False}
    data = (json.dumps(receipt, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    publish_freeze(output, data, conflict='different low-ramp pilot receipt already exists')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--ordinal', type=int, required=True)
    parser.add_argument('--capture-project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, ordinal=args.ordinal, project=args.capture_project, output=args.output),
                     sort_keys=True))


if __name__ == '__main__':
    main()
