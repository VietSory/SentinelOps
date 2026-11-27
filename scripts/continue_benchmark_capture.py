"""Continue registered holdout groups after the existing first controller exits."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def process_active(pid, start_ticks):
    """Recognize the original controller, not a later process reusing its PID."""
    try:
        stat = (Path('/proc') / str(pid) / 'stat').read_text()
    except FileNotFoundError:
        return False
    tail = stat[stat.rfind(')') + 2:].split()
    return tail[0] != 'Z' and tail[19] == start_ticks


def continue_groups(root, project, *, registration_sha256, source_commit, first_pid, first_start_ticks,
                    python=sys.executable):
    root, project = Path(root).resolve(), Path(project).resolve()
    if (not re.fullmatch('[0-9a-f]{64}', registration_sha256)
            or not re.fullmatch('[0-9a-f]{40}', source_commit)
            or type(first_pid) is not int or first_pid <= 1 or not str(first_start_ticks).isdigit()):
        raise ValueError('registered source and original controller identity required')
    registration = json.loads((root / 'capture/registration.json').read_bytes())
    import hashlib
    if (hashlib.sha256((root / 'capture/registration.json').read_bytes()).hexdigest() != registration_sha256
            or registration['capture_commit'] != source_commit):
        raise ValueError('capture registration differs from the requested source')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=project, text=True).strip()
    if head != source_commit:
        raise ValueError('capture checkout differs from its registered revision')
    environment = dict(os.environ, PYTHONPATH=str(project / 'src') + os.pathsep + str(project),
                       PYTHONDONTWRITEBYTECODE='1')
    prefix = [python, '-u', '-m', 'scripts.model_benchmark', '--root', str(root), '--protocol',
              str(root / 'freeze/protocol.json')]
    def run(arguments, log):
        with log.open('x') as stream:
            subprocess.run(prefix + arguments, cwd=project, env=environment, stdin=subprocess.DEVNULL,
                           stdout=stream, stderr=subprocess.STDOUT, check=True)
    with (root / 'capture/sequence.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('another capture sequence is active') from error
        while process_active(first_pid, str(first_start_ticks)):
            time.sleep(30)
        for ordinal in range(1, 6):
            group = f'bench-final-{ordinal:02d}'
            if ordinal > 1:
                run(['capture', '--group', group, '--registration-sha256', registration_sha256],
                    root / 'capture' / f'sequence-{group}-capture.log')
            run(['verify-group', '--group', group, '--registration-sha256', registration_sha256],
                root / 'capture' / f'sequence-{group}-verify.log')
            running = subprocess.check_output(['docker', 'ps', '-q', '--filter',
                'label=com.docker.compose.project=sentinelops-corpus-' + group], text=True)
            if running.strip():
                raise ValueError('verified group still has running containers: ' + group)
            print(json.dumps({'group': group, 'status': 'verified-and-stopped'}), flush=True)
        run(['seal-capture', '--registration-sha256', registration_sha256],
            root / 'capture/sequence-seal.log')
        print(json.dumps({'status': 'sealed', 'groups': 5}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--registration-sha256', required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--first-pid', type=int, required=True)
    parser.add_argument('--first-start-ticks', required=True)
    args = parser.parse_args()
    continue_groups(args.root, args.project, registration_sha256=args.registration_sha256,
                    source_commit=args.source_commit, first_pid=args.first_pid, first_start_ticks=args.first_start_ticks)


if __name__ == '__main__':
    main()
