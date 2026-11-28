"""Development benchmark front door; unavailable execution stages fail closed."""
import argparse
import json
from pathlib import Path

BLOCKED = {
    'fit': 'authorized-trainer-and-F5-receipts-required',
    'freeze-candidates': 'verified-fit-calibration-and-both-lane-receipts-required',
    'capture-preflight': 'approved-capture-protocol-and-adapter-required',
    'capture': 'capture-adapter-and-explicit-capture-authorization-required',
    'verify-group': 'admitted-capture-receipt-schema-and-verifier-required',
    'seal-capture': 'verified-complete-capture-groups-required',
    'score': 'admitted-dataset-candidate-event-lifecycle-required',
    'evaluate': 'admitted-dataset-candidate-event-lifecycle-required',
    'report': 'both-lane-report-adapters-and-verified-inputs-required',
    'verify-report': 'complete-report-manifest-and-verifier-required',
    'package': 'complete-verified-development-output-manifest-required',
}


def read_protocol(path):
    def reject_constant(value):
        raise ValueError('nonfinite JSON constant: ' + value)

    def unique_object(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError('duplicate JSON key')
        return value

    return json.loads(path.read_bytes(), parse_constant=reject_constant, object_pairs_hook=unique_object)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        'Lane B development stages require an admitted input descriptor. Shared '
        'capture, candidate freeze, and final reporting are blocked pending handoff.'))
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--protocol', required=True, type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('audit', 'fit', 'calibrate', 'score', 'evaluate'):
        sub = commands.add_parser(name)
        sub.add_argument('--lane', choices=('a', 'b'), required=True)
        if name != 'audit':
            sub.add_argument('--lane-b-input', type=Path)
        if name in ('fit', 'calibrate', 'score'):
            sub.add_argument('--lane-a-evidence', type=Path,
                             help='Verify existing Alibaba development artifacts against pinned evidence.')
        if name == 'score':
            sub.add_argument('--candidates-sha256')
            sub.add_argument('--registration-sha256')
            sub.add_argument('--seal-sha256')
            sub.add_argument('--capture-project', type=Path, default=Path(__file__).resolve().parents[1])
    for name in ('verify', 'package'):
        commands.add_parser(name)
    commands.add_parser('freeze-protocol').add_argument(
        '--lanes', choices=('a', 'b', 'ab'), default='ab',
        help='Freeze one lane before the other is ready. Lane B requires reporting.lane_b. Defaults to both.')
    seal = commands.add_parser('seal-capture')
    seal.add_argument('--registration-sha256')
    seal.add_argument('--capture-project', type=Path, default=Path(__file__).resolve().parents[1])
    preflight = commands.add_parser('capture-preflight')
    preflight.add_argument('--draft-matrix', action='store_true', help='Print the proposed holdout matrix and time budget.')
    preflight.add_argument('--candidates-sha256')
    preflight.add_argument('--pilot-root', type=Path)
    preflight.add_argument('--pilot-ordinal', type=int)
    preflight.add_argument('--capture-project', type=Path, default=Path(__file__).resolve().parents[1])
    candidates = commands.add_parser('freeze-candidates')
    candidates.add_argument('--lane-a-evidence', type=Path)
    candidates.add_argument('--lane-b-fit-sha256')
    for name in ('report', 'verify-report'):
        sub = commands.add_parser(name)
        sub.add_argument('--lane', choices=('a', 'b') if name == 'verify-report' else ('b',))
        sub.add_argument('--lane-b-input', type=Path)
        if name == 'verify-report':
            sub.add_argument('--candidates-sha256')
            sub.add_argument('--registration-sha256')
            sub.add_argument('--seal-sha256')
            sub.add_argument('--capture-project', type=Path, default=Path(__file__).resolve().parents[1])
    for name in ('capture', 'verify-group'):
        sub = commands.add_parser(name)
        sub.add_argument('--group', required=True)
        sub.add_argument('--registration-sha256')
        sub.add_argument('--capture-project', type=Path, default=Path(__file__).resolve().parents[1])
    prepare = commands.add_parser('prepare-detector-local')
    prepare.add_argument('--archive', required=True, type=Path)
    prepare.add_argument('--fit-sha256', required=True)
    args = parser.parse_args(argv)
    result = {'stage': args.command}
    if getattr(args, 'lane', None):
        result['lane'] = args.lane
    if hasattr(args, 'group'):
        result['group'] = args.group
    try:
        if args.command == 'prepare-detector-local':
            from sentinelops.ml.model_benchmark_detector_archive import prepare_detector_archive

            result['lane'] = 'b'
            result.update(prepare_detector_archive(args.root, args.archive, args.fit_sha256))
        elif args.command == 'capture-preflight' and args.draft_matrix:
            from scripts.draft_benchmark_holdout import draft_matrix

            result.update(draft_matrix())
        elif args.command == 'capture-preflight' and any(
                value is not None for value in (args.candidates_sha256, args.pilot_root, args.pilot_ordinal)):
            if not args.candidates_sha256 or args.pilot_root is None or args.pilot_ordinal is None:
                raise ValueError('capture preflight requires candidates SHA, pilot root and pilot ordinal')
            from scripts.register_benchmark_capture import run

            result.update(run(args.root, candidates_sha256=args.candidates_sha256, pilot_root=args.pilot_root,
                              pilot_ordinal=args.pilot_ordinal, capture_project=args.capture_project))
        elif args.command in ('capture', 'verify-group') and args.registration_sha256:
            from sentinelops.ml.model_benchmark_holdout_capture import capture_group, verify_group

            group = args.group if args.group.startswith('bench-final-') else 'bench-final-' + args.group
            action = capture_group if args.command == 'capture' else verify_group
            receipt = action(args.root, group, args.registration_sha256, project=args.capture_project)
            result.update(status='ready', group=group, verified_slots=len(receipt['slots']),
                          registration_sha256=args.registration_sha256)
        elif args.command == 'seal-capture' and args.registration_sha256:
            import hashlib
            from sentinelops.ml.model_benchmark_holdout_capture import seal_capture

            receipt = seal_capture(args.root, args.registration_sha256, project=args.capture_project)
            result.update(status='ready', verified_slots=receipt['slot_count'],
                          registration_sha256=args.registration_sha256, capture_sealed=True,
                          seal_sha256=hashlib.sha256((args.root / 'capture/sealed.json').read_bytes()).hexdigest())
        elif args.command in ('score', 'verify-report') and any(value is not None for value in
                (args.candidates_sha256, args.registration_sha256, args.seal_sha256)):
            if (args.lane != 'a' or getattr(args, 'lane_a_evidence', None) is not None or args.lane_b_input is not None
                    or not all((args.candidates_sha256, args.registration_sha256, args.seal_sha256))):
                raise ValueError('sealed scoring requires Lane A and all three pins without development inputs')
            from sentinelops.ml.model_benchmark_forecast_export import write_forecast_artifacts
            from sentinelops.ml.model_benchmark_forecast_replay import score_sealed

            scored = score_sealed(args.root, candidates_sha256=args.candidates_sha256,
                registration_sha256=args.registration_sha256, seal_sha256=args.seal_sha256,
                capture_project=args.capture_project)
            manifest = write_forecast_artifacts(args.root, scored, verify_only=args.command == 'verify-report')
            result.update(status='ready', scope=manifest['dataset'], runs=manifest['runs'],
                          row_counts=manifest['row_counts'], q07_status=manifest['q07_status'],
                          manifest='report/forecast-manifest.json', activation_allowed=False,
                          verified=args.command == 'verify-report')
        elif args.command == 'freeze-candidates':
            if not args.lane_a_evidence and not args.lane_b_fit_sha256:
                result.update(status='blocked', reasons=[BLOCKED[args.command]])
            else:
                from scripts.freeze_benchmark_candidates import run

                result.update(run(args.root, read_protocol(args.protocol), args.lane_a_evidence,
                                  args.lane_b_fit_sha256))
        elif getattr(args, 'lane_a_evidence', None) is not None:
            if args.lane != 'a' or args.lane_b_input is not None:
                raise ValueError('--lane-a-evidence requires --lane a without --lane-b-input')
            from scripts.verify_ltfp_development_stage import verify_stage

            result.update(verify_stage(args.command, args.root, read_protocol(args.protocol), args.lane_a_evidence))
        elif getattr(args, 'lane_b_input', None) is not None:
            if args.lane != 'b':
                raise ValueError('--lane-b-input requires --lane b')
            from sentinelops.ml.model_benchmark_detector_stage import run_detector_stage

            result.update(run_detector_stage(args.command, args.root, args.protocol,
                                             args.lane_b_input))
        elif args.command in BLOCKED:
            result.update(status='blocked', reasons=[BLOCKED[args.command]])
        elif args.command == 'calibrate':
            result.update(status='blocked', reasons=[
                'admitted-pinned-score-fit-split-source-receipt-schema-required' if args.lane == 'a'
                else 'lane-b-calibration-input-required'])
        else:
            from sentinelops.ml.model_benchmark_protocol import audit_protocol, freeze_protocol, verify_protocol
            if args.command == 'verify':
                result.update(verify_protocol(args.root))
            else:
                protocol = read_protocol(args.protocol)
                audit = audit_protocol(protocol, args.root)
                result.update(audit)
                if args.command == 'audit':
                    lane = audit['lanes'][args.lane]
                    result.update(status=lane['status'], reasons=lane['reasons'])
                else:
                    result.update(freeze_protocol(protocol, args.root, lanes=tuple(args.lanes)))
        if result.get('status') not in ('ready', 'blocked'):
            raise ValueError('protocol API returned an invalid stage status')
        output = json.dumps(result, sort_keys=True, allow_nan=False)
    except ModuleNotFoundError as error:
        if error.name != 'sentinelops.ml.model_benchmark_protocol':
            raise
        result.update(status='blocked', reasons=['protocol-module-not-integrated'])
        output = json.dumps(result, sort_keys=True, allow_nan=False)
    except (ValueError, OSError, KeyError, TypeError) as error:
        result.update(status='invalid', reasons=[str(error)])
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 1
    print(output)
    return 0 if result['status'] == 'ready' else 2


if __name__ == '__main__':
    raise SystemExit(main())
