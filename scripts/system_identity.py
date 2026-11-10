"""ADR-017: compute a system version's evidence identity mechanically from git.

The identity is read from the capture's source commit, never from the working tree,
because `main` keeps moving after a capture. It covers everything the evaluator reads
apart from captured data: configs, the transitive in-package import closure of the
evaluator (as git blob ids), routes, topology, service code and the workload recipe.

    python -m scripts.system_identity --commit <sha>
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = 'sentinelops.ml.alibaba_local_evaluation'
PLAN = 'configs/alibaba_local_evaluation.json'
# Every plan field that shapes captured traffic or the evidence count (local_corpus.recipe plus
# the healthy-prefault and group minimums and the corpus policy). Replacement budget is excluded.
WORKLOAD_FIELDS = ('kinds', 'entry_rps', 'maximum_delay_ms', 'healthy_active_seconds', 'abrupt_active_seconds',
                   'gradual_active_seconds', 'warmup_seconds', 'recovery_seconds',
                   'abrupt_fault_mode_by_ordinal_parity', 'seed_base', 'minimum_verified_healthy_prefault_minutes',
                   'minimum_groups', 'policy_sha256')
SHARED_BUILD_INPUTS = ('services/common', 'src', 'contracts', 'docker/Dockerfile.service', 'pyproject.toml')


def stable_id(value) -> str:
    """Same encoding as sentinelops.telemetry.core.stable_id."""
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


class Commit:
    def __init__(self, sha, root=ROOT):
        self.sha, self.root = sha, root

    def _git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True).stdout

    _objects = None

    def objects(self):
        """path -> object id for every blob and tree at the commit (one git call); {} if absent."""
        if self._objects is None:
            listed = subprocess.run(['git', 'ls-tree', '-r', '-t', '-z', self.sha], cwd=self.root,
                                    capture_output=True)
            self._objects = {}
            if listed.returncode == 0:
                for record in listed.stdout.split(b'\0'):
                    if record:
                        meta, path = record.split(b'\t', 1)
                        self._objects[path.decode()] = meta.split()[2].decode()
        return self._objects

    def exists(self, path):
        return path in self.objects()

    def read(self, path):
        return self._git('cat-file', 'blob', self.objects()[path])

    def object_id(self, path):
        return self.objects()[path]


def module_file(source, module):
    base = 'src/' + module.replace('.', '/')
    for candidate in (base + '.py', base + '/__init__.py'):
        if source.exists(candidate):
            return candidate
    return None


def import_closure(source, module=EVALUATOR):
    """Every sentinelops module file reachable from `module`, including imports inside functions."""
    seen, stack = set(), [module]
    while stack:
        path = module_file(source, stack.pop())
        if path is None or path in seen:
            continue
        seen.add(path)
        for node in ast.walk(ast.parse(source.read(path))):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('sentinelops'):
                stack.append(node.module)
                stack.extend(node.module + '.' + alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                stack.extend(alias.name for alias in node.names if alias.name.startswith('sentinelops'))
    return sorted(seen)


def business_routes(source):
    tree = ast.parse(source.read('src/sentinelops/telemetry/core.py'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'BUSINESS_ROUTES' for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('BUSINESS_ROUTES not found')


def downstream(topology, service):
    seen, stack = set(), list(topology.get(service, ()))
    while stack:
        item = stack.pop()
        if item not in seen:
            seen.add(item)
            stack.extend(topology.get(item, ()))
    return sorted(seen)


def compute(sha, root=ROOT):
    import yaml
    source = Commit(sha, root)
    plan = json.loads(source.read(PLAN))
    topology_bytes = source.read('configs/topology.yaml')
    topology = yaml.safe_load(topology_bytes)['dependencies']
    routes = business_routes(source)
    recipe = stable_id({key: plan[key] for key in WORKLOAD_FIELDS})
    return {
        'run_identity': {
            'source_profile': plan['source_profile'],
            'feature_set_version': plan['feature_set_version'],
            'slo_health_policy_sha256': stable_id(json.loads(source.read('configs/slo_health_policy.json'))),
            'scoring_sha256': stable_id(plan['scoring']),
            'base_protocol_sha256': stable_id(json.loads(source.read('configs/local_model_evaluation.json'))),
            'scoring_code_blobs': {path: source.object_id(path) for path in import_closure(source)},
        },
        'capture_identity': {
            'source_commit': sha,
            'evaluation_plan_sha256': stable_id(plan),
            'topology_sha256': hashlib.sha256(topology_bytes).hexdigest(),
        },
        'shared_build_inputs': {path: source.object_id(path) for path in SHARED_BUILD_INPUTS},
        'services': sorted(routes),
        'service_identity': {
            service: {
                'route': ' '.join(routes[service]),
                'downstream': downstream(topology, service),
                'code_tree': source.object_id('services/' + service.removesuffix('-service')),
                # score_by_route at this evaluator scores every service on every plan kind.
                'measured_kinds': list(plan['kinds']),
                'workload_recipe_sha256': recipe,
            } for service in sorted(routes)
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    print(json.dumps(compute(parser.parse_args().commit), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
