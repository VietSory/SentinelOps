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
    for name in ('freeze-protocol', 'verify', 'freeze-candidates', 'capture-preflight',
                 'seal-capture', 'package'):
        commands.add_parser(name)
    for name in ('report', 'verify-report'):
        sub = commands.add_parser(name)
        sub.add_argument('--lane', choices=('b',))
        sub.add_argument('--lane-b-input', type=Path)
    for name in ('capture', 'verify-group'):
        commands.add_parser(name).add_argument('--group', required=True)
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
                def reject_constant(value):
                    raise ValueError('nonfinite JSON constant: ' + value)
                def unique_object(pairs):
                    value = {}
                    for key, item in pairs:
                        if key in value:
                            raise ValueError('duplicate JSON key: ' + key)
                        value[key] = item
                    return value
                protocol = json.loads(args.protocol.read_text(), parse_constant=reject_constant,
                                      object_pairs_hook=unique_object)
                audit = audit_protocol(protocol, args.root)
                result.update(audit)
                if args.command == 'audit':
                    lane = audit['lanes'][args.lane]
                    result.update(status=lane['status'], reasons=lane['reasons'])
                elif audit['status'] == 'ready':
                    result.update(freeze_protocol(protocol, args.root))
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
