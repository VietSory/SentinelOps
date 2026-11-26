"""Bind reproduced detector development checks to a new protocol draft."""
import argparse
import json
from pathlib import Path
import tempfile

from scripts.verify_detector_development_support import run as verify_support
from sentinelops.ml.model_benchmark_protocol import _json_ref, audit_protocol
from sentinelops.public_data.alibaba import file_sha256


def run(root, *, input_revision, output_revision, fit_root, fit_sha256, support_path, support_sha256):
    root = Path(root).resolve()
    if any(type(value) is not int or value < 1 for value in (input_revision, output_revision)):
        raise ValueError('positive protocol draft revisions required')
    protocol_path = root / f'preflight/protocol-draft-v{input_revision}/protocol.json'
    output = root / f'preflight/protocol-draft-v{output_revision}'
    support_path = Path(support_path).resolve()
    if not support_path.is_relative_to(root) or file_sha256(support_path) != support_sha256:
        raise ValueError('trusted detector support receipt pin required')
    protocol = json.loads(protocol_path.read_bytes())
    previous = audit_protocol(protocol, root)
    if previous['lanes']['a']['status'] != 'ready' or any(item['test_id'].startswith('B') for item in protocol['checks']):
        raise ValueError('expected a Lane A-ready protocol without detector checks')
    reproduced = verify_support(root, protocol_path, fit_root, fit_sha256, support_path)
    if (file_sha256(support_path) != support_sha256 or reproduced['b02']['status'] != 'passed'
            or reproduced['b06']['status'] != 'passed' or reproduced['fixtures']['status'] != 'passed'):
        raise ValueError('reproduced detector development support is incomplete')
    if output.exists():
        raise FileExistsError(output)
    support = _json_ref(root, protocol['data']['support'])
    support['lanes']['b'] = {'status': 'ready', 'scope': 'real', 'dataset_scope': reproduced['scope'],
        'support_receipt': {'path': support_path.relative_to(root).as_posix(), 'sha256': support_sha256},
        'fit_sha256': fit_sha256, 'model_sha256': reproduced['model_sha256'],
        'b02': reproduced['b02'], 'b06': reproduced['b06'], 'limitations': reproduced['limitations']}
    with tempfile.TemporaryDirectory(prefix='.detector-support-', dir=output.parent) as temporary:
        stage = Path(temporary)

        def write(name, value):
            path = stage / name
            path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')
            return {'path': (output / name).relative_to(root).as_posix(), 'sha256': file_sha256(path)}

        protocol['data']['support'] = write('support.json', support)
        for number in range(1, 7):
            test_id = f'B{number:02}'
            check = {'test_id': test_id, 'status': 'passed',
                     'scope': 'real' if test_id in ('B02', 'B06') else 'fixture'}
            evidence = write(test_id + '.json', {**check,
                'source_manifest_sha256': protocol['code']['source_manifest']['sha256']})
            protocol['checks'].append({**check, 'evidence': evidence})
        write('protocol.json', protocol)
        stage.rename(output)
    result = audit_protocol(protocol, root)
    if result['status'] != 'ready':
        raise ValueError('detector admission did not clear the development protocol audit')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--input-revision', type=int, required=True)
    parser.add_argument('--output-revision', type=int, required=True)
    parser.add_argument('--fit-root', type=Path, required=True)
    parser.add_argument('--fit-sha256', required=True)
    parser.add_argument('--support-path', type=Path, required=True)
    parser.add_argument('--support-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, input_revision=args.input_revision, output_revision=args.output_revision,
                         fit_root=args.fit_root, fit_sha256=args.fit_sha256, support_path=args.support_path,
                         support_sha256=args.support_sha256), sort_keys=True))


if __name__ == '__main__':
    main()
