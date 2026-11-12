"""ADR-017: one source of business service names per system version, and evidence identity.

`configs/system_versions.json` declares each system version. The current version must
equal BUSINESS_ROUTES and the RCA topology. A contract file belongs to the current version
unless `frozen_contracts` pins it to an older version, so adding a service in a new
contract version leaves the old contract valid while any forgotten enumeration fails.

Each version's `computed` identity is recomputed here from git at the capture's source
commit by `scripts/system_identity.py`, never from the working tree, because `main` keeps
moving after a capture. Each version entry is pinned by hash. A version may carry
services forward only if their run identity, service identity, shared infrastructure
and shared build inputs equal the predecessor's.
"""
import copy
import hashlib
import json
from pathlib import Path
import re

import pytest
import yaml

from scripts.system_identity import Commit, compute, downstream, import_closure
from sentinelops.telemetry.core import BUSINESS_ROUTES

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / 'configs/system_versions.json').read_text())

# A declared version is immutable: its whole entry is pinned here. Pins are only ever added.
FROZEN_VERSIONS = {
    'system-v1': '710f9db5c614bf270dff985621004bb09b4a37b0080aea5ecbfbfbe1d14e6371',
    'system-v2': '109fe8d3b5c6f6e40dd957c4b66146814368ea30e013f4a08bed3ff52b43bc8a',
}
DECLARED = ('epoch', 'model_version', 'bundle_manifest_sha256', 'shared_infrastructure', 'carry_forward_from',
            'carried_services')
SERVICE_NAME = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*-service')


def entry_sha256(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def service_names(paths, ignored):
    """Every string list and scalar in a contract shaped like a service name (catches misspellings)."""
    def named(value):
        return isinstance(value, str) and value not in ignored and SERVICE_NAME.fullmatch(value)
    for path in paths:
        def walk(node, pointer):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield from walk(value, pointer + '/' + key)
            elif isinstance(node, list):
                if any(named(item) for item in node):
                    yield path.name, pointer, 'list', [item for item in node if named(item)]
                for index, value in enumerate(node):
                    if not isinstance(value, str):
                        yield from walk(value, f'{pointer}[{index}]')
            elif named(node):
                yield path.name, pointer, 'scalar', [node]
        yield from walk(json.loads(path.read_text()), '')


def carry_forward_errors(versions, name, entry):
    """A carried service keeps every input of its measurement (ADR-017 1a)."""
    declared, errors = entry['declared'], []
    source = declared['carry_forward_from']
    carried = declared['carried_services']
    if not carried:
        return errors
    if source not in versions:
        return [f'{name} carries services from undeclared {source}']
    old, new = versions[source], entry
    if (old['computed']['run_identity'] != new['computed']['run_identity']
            or old['declared']['model_version'] != declared['model_version']
            or old['declared']['bundle_manifest_sha256'] != declared['bundle_manifest_sha256']):
        errors.append(f'{name} run identity differs from {source}; no service can carry forward')
    if old['computed']['shared_build_inputs'] != new['computed']['shared_build_inputs']:
        errors.append(f'{name} shared build inputs differ from {source}; no service can carry forward')
    for service in carried:
        if (service not in old['services']
                or old['computed']['service_identity'].get(service) != new['computed']['service_identity'].get(service)
                or old['declared']['shared_infrastructure'].get(service)
                != declared['shared_infrastructure'].get(service)):
            errors.append(f'{name} cannot carry {service}: its measurement inputs differ from {source}')
    return errors


def registry_errors(registry, contract_paths, routes, topology, pins, recompute=None):
    versions, current, errors = registry['versions'], registry['current'], []
    if current not in versions:
        return ['current system version is not declared']
    if set(versions[current]['services']) != set(routes):
        errors.append(f'{current} services differ from BUSINESS_ROUTES {sorted(routes)}')
    if {name for name in topology if name.endswith('-service')} != set(routes):
        errors.append('topology services differ from BUSINESS_ROUTES')
    for service, edges in topology.items():
        if not all(item in topology for item in edges):
            errors.append(f'{service} depends on an undeclared component')
    for name, entry in versions.items():
        if pins.get(name) != entry_sha256(entry):
            errors.append(f'{name} differs from its frozen pin; declare a new system version instead')
        declared = entry.get('declared', {})
        if any(key not in declared for key in DECLARED) or not declared.get('model_version'):
            errors.append(f'{name} lacks declared fields {list(DECLARED)}')
            continue
        computed = entry.get('computed', {})
        if recompute is not None:
            commit = computed.get('capture_identity', {}).get('source_commit')
            actual = recompute(commit)
            for block in ('run_identity', 'capture_identity', 'shared_build_inputs', 'service_identity'):
                if computed.get(block) != actual[block]:
                    errors.append(f'{name} {block} differs from scripts/system_identity.py at {commit}')
            if sorted(entry['services']) != actual['services']:
                errors.append(f'{name} services differ from BUSINESS_ROUTES at {commit}')
        errors.extend(carry_forward_errors(versions, name, entry))
    # The current version must describe the repository's present topology and routes.
    for service in versions[current]['services']:
        identity = versions[current].get('computed', {}).get('service_identity', {}).get(service, {})
        if identity.get('downstream') != downstream(topology, service):
            errors.append(f'{current} {service} downstream differs from the topology call path')
        if isinstance(routes.get(service), tuple) and identity.get('route') != ' '.join(routes[service]):
            errors.append(f'{current} {service} route differs from BUSINESS_ROUTES')
    names = {path.name for path in contract_paths}
    for name, version in registry['frozen_contracts'].items():
        if name not in names:
            errors.append(f'frozen contract {name} does not exist')
        if version not in versions or version == current:
            errors.append(f'frozen contract {name} must pin an older declared version, not {version}')
    found = 0
    for name, pointer, shape, listed in service_names(contract_paths, set(registry.get('non_service_tokens', ()))):
        version = registry['frozen_contracts'].get(name, current)
        expected = set(versions[version]['services'])
        if shape == 'list':
            found += 1
            if set(listed) != expected:
                errors.append(f'{name}{pointer} ({version}) lists {sorted(listed)}, expected {sorted(expected)}')
        elif listed[0] not in expected:
            errors.append(f'{name}{pointer} ({version}) names unknown service {listed[0]}')
    if not found:
        errors.append('expected schema enumerations of business services')
    return errors


def test_repository_registry_matches_git_and_current_system():
    commit = REGISTRY['versions']['system-v1']['computed']['capture_identity']['source_commit']
    if not Commit(commit).exists('configs/topology.yaml'):
        pytest.fail('ADR-017 identity needs full git history (actions/checkout fetch-depth: 0)')
    topology = yaml.safe_load((ROOT / 'configs/topology.yaml').read_text())['dependencies']
    paths = sorted((ROOT / 'contracts').glob('*.json'))
    assert registry_errors(REGISTRY, paths, BUSINESS_ROUTES, topology, FROZEN_VERSIONS, recompute=compute) == []
    # A topology edit needs a new system version, never a silent edit of the current one.
    current = REGISTRY['versions'][REGISTRY['current']]['computed']['capture_identity']
    assert current['topology_sha256'] == hashlib.sha256((ROOT / 'configs/topology.yaml').read_bytes()).hexdigest()


def test_scoring_code_closure_follows_imports_into_feature_generation():
    # Codex review 5: alibaba_live calls alibaba_training.service_windows_fast; a hand list missed it.
    commit = REGISTRY['versions']['system-v1']['computed']['capture_identity']['source_commit']
    closure = import_closure(Commit(commit))
    for path in ('src/sentinelops/ml/alibaba_training.py', 'src/sentinelops/ml/alibaba_live.py',
                 'src/sentinelops/ml/local_evaluation.py', 'src/sentinelops/ml/detector_evaluation.py',
                 'src/sentinelops/experiments/labels.py', 'src/sentinelops/telemetry/prometheus.py'):
        assert path in closure
    assert set(closure) == set(REGISTRY['versions']['system-v1']['computed']['run_identity']['scoring_code_blobs'])


@pytest.mark.parametrize('edit', ['feature-generation', 'risk-threshold', 'workload-delay', 'slo-policy'])
def test_identity_procedure_detects_each_measurement_input(tmp_path, edit):
    """Replay Codex's cases on a throwaway clone of the capture commit: edit, commit, recompute."""
    import subprocess
    commit = REGISTRY['versions']['system-v1']['computed']['capture_identity']['source_commit']
    clone = tmp_path / 'clone'
    subprocess.run(['git', 'clone', '-q', '--shared', '--no-checkout', str(ROOT), str(clone)], check=True)
    subprocess.run(['git', '-C', str(clone), 'checkout', '-q', commit], check=True)
    def edit_json(path, change):
        data = json.loads((clone / path).read_text())
        change(data)
        (clone / path).write_text(json.dumps(data))
    if edit == 'feature-generation':  # alibaba_live -> alibaba_training.service_windows_fast
        target = clone / 'src/sentinelops/ml/alibaba_training.py'
        target.write_text(target.read_text() + '\n# fixture edit of feature generation\n')
    if edit == 'risk-threshold':
        edit_json('configs/local_model_evaluation.json', lambda d: d.update(risk_threshold=0.7))
    if edit == 'workload-delay':
        edit_json('configs/alibaba_local_evaluation.json', lambda d: d.update(maximum_delay_ms=1000))
    if edit == 'slo-policy':
        edit_json('configs/slo_health_policy.json', lambda d: d.update(fixture_edit=True))
    env = {'GIT_AUTHOR_NAME': 'fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
           'GIT_COMMITTER_NAME': 'fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid', 'PATH': '/usr/bin:/bin'}
    subprocess.run(['git', '-C', str(clone), '-c', 'core.hooksPath=/dev/null', 'commit', '-qam', 'fixture'],
                   check=True, env=env)
    edited = subprocess.run(['git', '-C', str(clone), 'rev-parse', 'HEAD'], check=True, capture_output=True,
                            text=True).stdout.strip()
    before, after = compute(commit, clone), compute(edited, clone)
    assert before['run_identity'] == REGISTRY['versions']['system-v1']['computed']['run_identity']
    if edit == 'workload-delay':
        assert all(before['service_identity'][s]['workload_recipe_sha256'] != after['service_identity'][s][
            'workload_recipe_sha256'] for s in before['service_identity'])
    else:
        assert before['run_identity'] != after['run_identity']


V1 = ['checkout-service', 'inventory-service', 'payment-service']
V2 = V1 + ['fraud-service']
TOPOLOGY_V1 = {'checkout-service': ['payment-service', 'inventory-service'], 'payment-service': ['rds'],
               'inventory-service': ['rds'], 'rds': []}
TOPOLOGY_V2 = {'checkout-service': ['payment-service', 'inventory-service'], 'payment-service': ['rds'],
               'inventory-service': ['rds'], 'fraud-service': ['rds'], 'rds': []}
KINDS_V1 = ['healthy', 'payment-abrupt', 'inventory-abrupt', 'payment-gradual', 'inventory-gradual']


def contracts(tmp_path, files):
    for name, services in files.items():
        body = {'properties': {'service': {'enum': services}}} if isinstance(services, list) else services
        (tmp_path / name).write_text(json.dumps(body))
    return sorted(tmp_path.glob('*.json'))


def version(services, topology, kinds=KINDS_V1, carried=(), source=None):
    return {'services': services,
            'declared': {'epoch': 'fixture', 'model_version': 'm', 'bundle_manifest_sha256': 'b',
                         'shared_infrastructure': {s: ['rds'] for s in services},
                         'carry_forward_from': source, 'carried_services': list(carried)},
            'computed': {'run_identity': {'scoring_sha256': 's', 'scoring_code_blobs': {'a.py': '1'}},
                         'capture_identity': {'source_commit': 'c'},
                         'shared_build_inputs': {'src': 't'},
                         'service_identity': {s: {'route': 'GET /' + s, 'downstream': downstream(topology, s),
                                                  'code_tree': 'tree-' + s, 'measured_kinds': list(kinds),
                                                  'workload_recipe_sha256': 'r'} for s in services}}}


def registry(frozen, v2=None):
    versions = {'system-v1': version(V1, TOPOLOGY_V1), 'system-v2': v2 or version(V2, TOPOLOGY_V2)}
    return {'current': 'system-v2', 'frozen_contracts': frozen, 'versions': versions}


def pins(declared):
    return {name: entry_sha256(entry) for name, entry in declared['versions'].items()}


def test_adding_a_service_in_a_new_contract_version_keeps_the_old_contract_valid(tmp_path):
    paths = contracts(tmp_path, {'health-signal.schema.json': V1, 'health-signal-v2.schema.json': V2})
    declared = registry({'health-signal.schema.json': 'system-v1'})
    assert registry_errors(declared, paths, dict.fromkeys(V2), TOPOLOGY_V2, pins(declared)) == []


@pytest.mark.parametrize('case', ['current-contract-not-updated', 'old-contract-not-pinned', 'pin-to-current',
                                  'pin-missing-file', 'routes-not-updated', 'topology-not-updated',
                                  'all-names-misspelt', 'one-name-misspelt', 'misspelt-const',
                                  'missing-declared-model', 'v1-edited-in-place', 'new-version-not-pinned',
                                  'downstream-stale'])
def test_guard_still_catches_an_incomplete_service_addition(tmp_path, case):
    files = {'health-signal.schema.json': V1, 'health-signal-v2.schema.json': V2}
    frozen = {'health-signal.schema.json': 'system-v1'}
    routes, topology = dict.fromkeys(V2), dict(TOPOLOGY_V2)
    if case == 'current-contract-not-updated':
        files['feature-envelope-v2.schema.json'] = V1
    if case == 'old-contract-not-pinned':
        frozen = {}
    if case == 'pin-to-current':
        frozen = {'health-signal.schema.json': 'system-v2'}
    if case == 'pin-missing-file':
        frozen['gone.schema.json'] = 'system-v1'
    if case == 'all-names-misspelt':
        files['action-v2.schema.json'] = ['paymant-service']
    if case == 'one-name-misspelt':
        files['action-v2.schema.json'] = ['checkout-service', 'inventory-service', 'paymant-service', 'fraud-service']
    if case == 'misspelt-const':
        files['action-v2.schema.json'] = {'properties': {'deployment': {'const': 'paymant-service'}}}
    if case == 'routes-not-updated':
        routes = dict.fromkeys(V1)
    if case == 'topology-not-updated':
        topology.pop('fraud-service')
    declared = registry(frozen)
    pinned = pins(declared)
    if case == 'missing-declared-model':
        declared['versions']['system-v2']['declared']['model_version'] = ''
        pinned = pins(declared)
    if case == 'v1-edited-in-place':
        v1 = declared['versions']['system-v1']['computed']
        v1['capture_identity']['topology_sha256'] = 'edited'
        v1['service_identity']['checkout-service']['downstream'].append('rds-direct')
    if case == 'new-version-not-pinned':
        pinned.pop('system-v2')
    if case == 'downstream-stale':
        topology['checkout-service'] = ['payment-service', 'inventory-service', 'rds']
        topology['inventory-service'] = []
    assert registry_errors(declared, contracts(tmp_path, files), routes, topology, pinned)


def carry(v2, tmp_path):
    files = {'health-signal.schema.json': V1, 'health-signal-v2.schema.json': V2}
    declared = registry({'health-signal.schema.json': 'system-v1'}, v2)
    errors = registry_errors(declared, contracts(tmp_path, files), dict.fromkeys(V2), TOPOLOGY_V2, pins(declared))
    return [e for e in errors if 'carr' in e or 'identity' in e or 'inputs' in e]


def test_carry_forward_is_allowed_only_with_identical_measurement_inputs(tmp_path):
    assert carry(version(V2, TOPOLOGY_V2, carried=['checkout-service'], source='system-v1'), tmp_path) == []


@pytest.mark.parametrize('change', ['new-fault-kinds', 'code', 'shared-build', 'run', 'model', 'infrastructure',
                                    'unknown-source', 'service-not-in-source'])
def test_carry_forward_is_refused_when_any_measurement_input_differs(change, tmp_path):
    # Codex review 5: with the current evaluator every service is scored on every kind, so adding a
    # new service's fault kinds changes every old service's measurement and blocks carry-forward
    # until a per-service selection path exists and is pre-registered in the plan.
    kinds = KINDS_V1 + ['fraud-abrupt', 'fraud-gradual'] if change == 'new-fault-kinds' else KINDS_V1
    carried = ['fraud-service'] if change == 'service-not-in-source' else ['payment-service']
    v2 = version(V2, TOPOLOGY_V2, kinds=kinds, carried=carried,
                 source='system-v9' if change == 'unknown-source' else 'system-v1')
    if change == 'code':
        v2['computed']['service_identity']['payment-service']['code_tree'] = 'changed'
    if change == 'shared-build':
        v2['computed']['shared_build_inputs']['src'] = 'changed'
    if change == 'run':
        v2['computed']['run_identity']['scoring_code_blobs'] = {'a.py': '2'}
    if change == 'model':
        v2['declared']['model_version'] = 'm2'
    if change == 'infrastructure':
        v2['declared']['shared_infrastructure']['payment-service'] = ['rds', 'fraud-db']
    assert carry(v2, tmp_path)


def test_editing_the_repository_system_v1_in_place_fails_the_pin():
    edited = copy.deepcopy(REGISTRY['versions']['system-v1'])
    edited['computed']['service_identity']['checkout-service']['downstream'].append('rds')
    edited['computed']['capture_identity']['topology_sha256'] = '0' * 64
    assert entry_sha256(edited) != FROZEN_VERSIONS['system-v1']
