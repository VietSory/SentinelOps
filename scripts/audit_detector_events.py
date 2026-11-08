"""Recompute an ADR-015 evaluation-only detector-event epoch; never fit."""
import argparse
import json
from pathlib import Path

from sentinelops.experiments.local_corpus import DETECTOR_PLAN, load_plan
from sentinelops.ml.local_data import audit_detector_events, freeze_detector_events
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--plan', type=Path, default=DETECTOR_PLAN)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--freeze', action='store_true',
                        help='Bind only a fully recomputed READY_FOR_EVALUATION epoch; blocks further acquisition.')
    args = parser.parse_args()
    store, plan = ArtifactStore(args.root), load_plan(args.plan)
    report, _, _ = audit_detector_events(store, plan)
    reference = store.write('manifests', report)
    if args.freeze:
        if freeze_detector_events(store, plan, report) != reference:
            raise ValueError('frozen detector admission differs from the audited report')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump({'detector_report_ref': reference, 'report': report}, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'detector_report_ref': reference, **{k: report[k] for k in (
        'status', 'registered_groups', 'complete_groups', 'verified_completed_runs', 'counts', 'failed_conditions')}}))


if __name__ == '__main__':
    main()
