"""Register the frozen holdout design before any holdout capture."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import sentinelops

from scripts.draft_benchmark_holdout import draft_matrix
from scripts.verify_low_ramp_pilot import run as verify_pilot
from sentinelops.experiments.local_corpus import acquisition_lock, clean_source
from sentinelops.ml.model_benchmark_holdout import verify_candidate_freeze, verify_capture_registration
from sentinelops.ml.model_benchmark_protocol import _bytes, publish_freeze
from sentinelops.telemetry.core import stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore


def _capture_adapter_ready():
    try:
        from sentinelops.ml.model_benchmark_holdout_capture import capture_group
    except ModuleNotFoundError as error:
        if error.name != 'sentinelops.ml.model_benchmark_holdout_capture':
            raise
        return False
    return callable(capture_group)


def run(root, *, candidates_sha256, pilot_root, pilot_ordinal, capture_project):
    if not _capture_adapter_ready():
        return {'status': 'blocked', 'reasons': ['holdout-capture-adapter-required']}
    root, pilot_root, capture_project = (Path(path).resolve() for path in (root, pilot_root, capture_project))
    if pilot_root == root or not pilot_root.is_relative_to(root):
        raise ValueError('pilot archive must be inside the benchmark root')
    project = Path(__file__).resolve().parents[1]
    if not Path(sentinelops.__file__).samefile(project / 'src/sentinelops/__init__.py'):
        raise ValueError('registration must import its checkout source')
    commit = clean_source(project)
    matrix = draft_matrix()['matrix']
    matrix_sha256 = stable_id(matrix)
    with acquisition_lock(ArtifactStore(root)), acquisition_lock(ArtifactStore(pilot_root)):
        frozen = verify_candidate_freeze(root, candidates_sha256)
        pilot_path = pilot_root / 'benchmark-receipts' / (commit + '.json')
        pilot = verify_pilot(pilot_root, ordinal=pilot_ordinal, project=capture_project, output=pilot_path)
        if pilot['status'] != 'ready' or pilot['checker_source_commit'] != commit:
            raise ValueError('verified no-breach pilot on the capture revision is required')
        if clean_source(project) != commit:
            raise ValueError('capture source changed during registration')
        directory = root / 'capture'
        registration_path = directory / 'registration.json'
        matrix_path = directory / 'matrix.json'
        if not registration_path.exists() and directory.exists():
            if any(item.name != 'matrix.json' for item in directory.iterdir()):
                raise ValueError('unregistered holdout artifacts already exist')
        publish_freeze(matrix_path, _bytes(matrix), conflict='different holdout matrix already registered')
        pilot_ref = {'path': pilot_path.relative_to(root).as_posix(),
                     'sha256': hashlib.sha256(pilot_path.read_bytes()).hexdigest()}
        expected = {'schema_version': 'benchmark-holdout-registration/v1',
                    'protocol_sha256': frozen['protocol_sha256'],
                    'protocol_file_sha256': hashlib.sha256((root / 'freeze/protocol.json').read_bytes()).hexdigest(),
                    'candidates_sha256': candidates_sha256, 'matrix_sha256': matrix_sha256,
                    'capture_commit': commit, 'pilot_receipt': pilot_ref}
        if registration_path.exists():
            registration = json.loads(registration_path.read_bytes())
            if {key: value for key, value in registration.items() if key != 'registered_at'} != expected:
                raise ValueError('different holdout design already registered')
        else:
            registration = {**expected, 'registered_at': timestamp(datetime.now(timezone.utc))}
            publish_freeze(registration_path, _bytes(registration), conflict='different holdout design already registered')
        pin = hashlib.sha256(registration_path.read_bytes()).hexdigest()
        verify_capture_registration(root, registration_sha256=pin, matrix_sha256=matrix_sha256,
            candidates_sha256=candidates_sha256, capture_commit=commit, frozen=frozen)
    return {'status': 'ready', 'registration_sha256': pin, 'matrix_sha256': matrix_sha256,
            'capture_commit': commit, 'holdout_opened': False, 'activation_allowed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--candidates-sha256', required=True)
    parser.add_argument('--pilot-root', type=Path, required=True)
    parser.add_argument('--pilot-ordinal', type=int, required=True)
    parser.add_argument('--capture-project', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, candidates_sha256=args.candidates_sha256, pilot_root=args.pilot_root,
                         pilot_ordinal=args.pilot_ordinal, capture_project=args.capture_project), sort_keys=True))


if __name__ == '__main__':
    main()
