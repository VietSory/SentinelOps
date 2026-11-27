"""Verify one prospectively registered Alibaba development healthy capture."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import sentinelops

from scripts.check_ltfp_graph_parity import check_worker
from sentinelops.experiments.local_corpus import (
    clean_source,
    group_name,
    load_plan,
    read_binding,
    registration_path,
)
from sentinelops.ml.local_data import verify_capture
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore


def run(root, project, ordinal):
    store = ArtifactStore(root)
    project = Path(project).resolve()
    checker_project = Path(__file__).resolve().parents[1]
    if not Path(sentinelops.__file__).samefile(checker_project / 'src/sentinelops/__init__.py'):
        raise ValueError('worker replay must import the checker checkout source')
    checker_commit = clean_source(checker_project)
    plan = load_plan(project / 'configs/alibaba_local_evaluation.json')
    group = group_name(plan, 'development-validation', ordinal)
    slot = group + '-healthy'
    _, session = read_binding(store, registration_path(store, group))
    environment = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(project / 'src'), str(project)))}
    end_context_ref = subprocess.check_output(
        [sys.executable, '-m', 'scripts.inspect_local_capture', '--root', str(store.root), '--slot', slot],
        cwd=project, env=environment, text=True).strip()
    verified = verify_capture(store, plan, session, 'healthy', project=project)
    source = store.read(verified['source_result_ref'])
    run_path = store.root / source['run_directory'] / 'run.json'
    run_bytes = run_path.read_bytes()
    document = json.loads(run_bytes)
    if stable_id(document) != source['run_sha256']:
        raise ValueError('source run changed after admission')
    topology = subprocess.check_output(
        ['git', 'show', session['source_commit'] + ':configs/topology.yaml'], cwd=project)
    parity = check_worker(run_path, expected_run_sha256=hashlib.sha256(run_bytes).hexdigest(),
                          expected_topology_sha256=hashlib.sha256(topology).hexdigest(), project=project)
    if clean_source(checker_project) != checker_commit:
        raise ValueError('worker replay source changed during verification')
    evidence = {
        'test_id': 'A07', 'status': 'passed', 'dataset_scope': 'alibaba-local-development-validation',
        'source_result_ref': verified['source_result_ref'], 'end_context_ref': end_context_ref,
        'run_id': verified['run_id'], 'source_commit': session['source_commit'],
        'checker_source_commit': checker_commit,
        'checker_sha256': hashlib.sha256(Path(__file__).with_name('check_ltfp_graph_parity.py').read_bytes()).hexdigest(),
        'parity': parity,
    }
    reference = store.write('manifests', evidence)
    return {'test_id': 'A07', 'status': 'passed', 'run_id': verified['run_id'],
            'evidence_ref': reference, 'max_abs_error': parity['max_abs_error']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--capture-project', type=Path, required=True)
    parser.add_argument('--ordinal', type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.capture_project, args.ordinal), sort_keys=True))


if __name__ == '__main__':
    main()
