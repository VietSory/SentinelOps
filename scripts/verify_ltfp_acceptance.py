"""Run fixture checks and bind their receipts to a blocked Lane A protocol draft."""
import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import sentinelops

from sentinelops.ml.model_benchmark_protocol import audit_protocol


CHECKS = {
    'A01': ['tests/test_latency_forecast.py::test_a01_latency_threshold_is_strict_and_onset_uses_first_bucket'],
    'A02': ['tests/test_latency_forecast.py::test_a02_forecast_onset_interval_is_open_left_and_closed_right',
            'tests/test_latency_forecast.py::test_a02_http_errors_do_not_create_latency_precursors'],
    'A03': ['tests/test_latency_forecast.py::test_a03_missing_evidence_stays_unknown_with_reason',
            'tests/test_latency_forecast.py::test_a03_late_cutoff_does_not_backdate_forecast',
            'tests/test_latency_forecast.py::test_a03_already_breaching_history_is_not_healthy',
            'tests/test_latency_forecast.py::test_a03_future_suffix_cannot_change_features_or_prediction'],
    'A04': ['tests/test_ltfp_cohort_audit.py::test_inclusive_dependency_bounds_and_disjoint_partitions',
            'tests/test_ltfp_cohort_audit.py::test_graph_rejects_cross_split_edges_and_future_topology',
            'tests/test_model_benchmark_protocol.py::test_cross_split_neighbor_and_unknown_parent_rejected',
            'tests/test_model_benchmark_protocol.py::test_exact_dependency_boundaries_and_purge'],
    'A05': ['tests/test_ltfp_graph.py::test_a05_bidirectional_attention_preserves_directed_topology_and_self_only'],
    'A06': ['tests/test_ltfp_graph.py::test_permutation_is_id_stable_and_masked_node_does_not_hide_other_targets',
            'tests/test_ltfp_model.py::test_roundtrip_determinism_permutation_and_no_mutation',
            'tests/test_ltfp_model.py::test_self_only_and_missing_neighbor'],
    'A09': ['tests/test_ltfp_model.py::test_unseen_topology_requires_caller_pin_and_preserves_training_identity',
            'tests/test_ltfp_graph.py::test_cutoff_excludes_late_and_future_observations_and_topology'],
}


def _bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _test_results(xml, checks):
    observed = set()
    for case in ET.fromstring(xml).iter('testcase'):
        if any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped')):
            raise ValueError('failed or skipped fixture check')
        observed.add((case.attrib['classname'].split('.')[-1], case.attrib['name'].split('[')[0]))
    expected = {(Path(path).stem, name) for targets in checks.values() for target in targets
                for path, name in [target.split('::')]}
    if not expected <= observed:
        raise ValueError('fixture check missing from pytest results')
    return len(list(ET.fromstring(xml).iter('testcase')))


def run(root):
    root = Path(root).resolve()
    repo = Path.cwd().resolve()
    if not Path(sentinelops.__file__).samefile(repo / 'src/sentinelops/__init__.py'):
        raise ValueError('fixture checks must import the workspace source')
    output = root / 'preflight/protocol-draft-v3'
    if output.exists():
        raise ValueError('protocol draft already exists')
    base = json.loads((root / 'preflight/protocol-draft-v1/protocol.json').read_text())
    if audit_protocol(base, root)['status'] != 'blocked' or base['checks']:
        raise ValueError('unmodified blocked protocol draft required')
    paths = sorted({p for directory in ('src/sentinelops', 'scripts', 'tests')
                    for p in (repo / directory).rglob('*.py')} |
                   {p for p in (repo / 'configs').rglob('*.json')} |
                   {repo / 'pyproject.toml', repo / 'requirements-ltfp-cpu.txt',
                    repo / 'docs/delivery/MODEL_BENCHMARK_RUNBOOK.md',
                    repo / 'docs/delivery/MODEL_BENCHMARK_PLAN.md'})
    sources = {str(path.relative_to(repo)): path.read_bytes() for path in paths}
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.protocol-checks-', dir=output.parent))
    prefix = output.relative_to(root)
    def write(name, content):
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {'path': str(prefix / name), 'sha256': _digest(content)}
    try:
        manifest = write('source.json', _bytes({'files': [
            write('source/' + name, content) for name, content in sources.items()]}))
        junit = stage / 'pytest.xml'
        targets = list(dict.fromkeys(target for group in CHECKS.values() for target in group))
        command = [sys.executable, '-m', 'pytest', '-q', '--disable-warnings',
                   '--junitxml=' + str(junit), *targets]
        result = subprocess.run(command, cwd=repo, capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError('fixture checks failed: ' + (result.stdout + result.stderr)[-2000:])
        count = _test_results(junit.read_bytes(), CHECKS)
        if any(not (repo / name).is_file() or (repo / name).read_bytes() != content
               for name, content in sources.items()):
            raise ValueError('workspace source changed during fixture checks')
        protocol = dict(base)
        protocol['code'] = {**base['code'], 'source_manifest': manifest}
        protocol['checks'] = []
        for test_id in CHECKS:
            evidence = dict(test_id=test_id, status='passed', scope='fixture',
                            source_manifest_sha256=manifest['sha256'])
            protocol['checks'].append({**{key: evidence[key] for key in ('test_id', 'status', 'scope')},
                                       'evidence': write(test_id + '.json', _bytes(evidence))})
        write('test-results.json', _bytes({'scope': 'fixture-only', 'source_manifest_sha256': manifest['sha256'],
            'checks': CHECKS, 'test_cases': count, 'junit_sha256': _digest(junit.read_bytes()),
            'python': platform.python_version(), 'packages': {name: version(name) for name in
                ('pytest', 'numpy', 'pandas', 'pyarrow', 'scikit-learn', 'xgboost-cpu', 'torch')},
            'limitations': ['Does not establish A07, A08, external topology or heldout performance.']}))
        write('pytest-output.txt', result.stdout.encode() + result.stderr.encode())
        write('protocol.json', _bytes(protocol))
        stage.rename(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    audited = audit_protocol(protocol, root)
    if audited['status'] != 'blocked':
        raise ValueError('fixture-only draft must remain blocked')
    return dict(status=audited['status'], protocol=str(output / 'protocol.json'),
                protocol_sha256=audited['protocol_sha256'], test_cases=count, reasons=audited['reasons'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.root), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
