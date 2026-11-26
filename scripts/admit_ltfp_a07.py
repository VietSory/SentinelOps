"""Bind a completed A07 worker replay receipt to the Lane A development protocol."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from sentinelops.ml.model_benchmark_protocol import audit_protocol, validate_worker_replay
from sentinelops.telemetry.store import ArtifactStore


def run(root, capture_root, evidence_ref, *, input_revision=4, output_revision=5):
    root = Path(root).resolve()
    capture = ArtifactStore(capture_root)
    evidence = capture.read(evidence_ref)
    if not evidence_ref.startswith('artifact://manifests/'):
        raise ValueError('expected an A07 manifest reference')
    validate_worker_replay(evidence)
    replay_path = capture.root / evidence_ref.removeprefix('artifact://')
    replay_ref = {'path': replay_path.relative_to(root).as_posix(),
                  'sha256': evidence_ref.rsplit('/', 1)[1].removesuffix('.json')}
    source = capture.read(evidence['source_result_ref'])
    context = capture.read(evidence['end_context_ref'])
    if source['status'] != 'completed' or context['source_result_ref'] != evidence['source_result_ref']:
        raise ValueError('A07 receipt does not match the completed capture context')
    run_path = (capture.root / source['run_directory'] / 'run.json').resolve()
    if not run_path.is_relative_to(capture.root):
        raise ValueError('capture run escapes the archive')
    if hashlib.sha256(run_path.read_bytes()).hexdigest() != evidence['parity']['run_sha256']:
        raise ValueError('A07 replay does not match the captured run')
    preflight = root / 'preflight'
    if any(type(value) is not int or value < 1 for value in (input_revision, output_revision)):
        raise ValueError('positive protocol draft revisions required')
    protocol = json.loads((preflight / f'protocol-draft-v{input_revision}/protocol.json').read_bytes())
    if audit_protocol(protocol, root)['lanes']['a']['reasons'] != ['A07: evidence missing']:
        raise ValueError('expected an audited protocol awaiting only A07')
    output = preflight / f'protocol-draft-v{output_revision}'
    if output.exists():
        raise FileExistsError(output)
    check = {'test_id': 'A07', 'status': 'passed', 'scope': 'real'}
    manifest = {'path': (output / 'A07.json').relative_to(root).as_posix()}
    with tempfile.TemporaryDirectory(prefix='.worker-replay-', dir=preflight) as temporary:
        stage = Path(temporary)
        receipt = {**check, 'source_manifest_sha256': protocol['code']['source_manifest']['sha256'],
                   'worker_replay': replay_ref}
        data = json.dumps(receipt, sort_keys=True, indent=2).encode() + b'\n'
        (stage / 'A07.json').write_bytes(data)
        manifest['sha256'] = hashlib.sha256(data).hexdigest()
        protocol['checks'].append({**check, 'evidence': manifest})
        (stage / 'protocol.json').write_text(json.dumps(protocol, sort_keys=True, indent=2) + '\n')
        stage.rename(output)
    result = audit_protocol(protocol, root)
    if result['lanes']['a']['status'] != 'ready':
        raise ValueError('A07 admission did not clear the Lane A development audit')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--capture-root', required=True, type=Path)
    parser.add_argument('--evidence-ref', required=True)
    parser.add_argument('--input-revision', type=int, default=4)
    parser.add_argument('--output-revision', type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.capture_root, args.evidence_ref, input_revision=args.input_revision,
                         output_revision=args.output_revision), sort_keys=True))


if __name__ == '__main__':
    main()
