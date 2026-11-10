"""ADR-017 decision 5: business service names have one source per system version.

`configs/system_versions.json` declares each system version's services. The current
version must equal BUSINESS_ROUTES and the RCA topology. A contract file belongs to the
current version unless it is listed in `frozen_contracts`, which pins an older contract
to the version it was written for. Adding a service in a new contract version therefore
leaves the old contract valid, while forgetting any current enumeration fails here.
"""
import hashlib
import json
from pathlib import Path
import re

import pytest
import yaml

from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / 'configs/system_versions.json').read_text())


# ADR-017 amendment 1 (1a, revised after the second Codex review). run_identity must be equal
# for any carry-forward; capture_identity only describes the capture; service_identity is the
# per-service equivalence a carried service must keep.
RUN_IDENTITY = ('model_version', 'bundle_manifest_sha256', 'source_profile', 'slo_health_policy_sha256',
                'scoring_sha256')
CAPTURE_IDENTITY = ('epoch', 'source_commit', 'evaluation_plan_sha256', 'topology_sha256')
SERVICE_IDENTITY = ('route', 'downstream', 'shared_infrastructure', 'code_tree', 'workload_kinds')
# A declared version is immutable: its whole entry is pinned here. Editing system-v1 in place
# (e.g. a new edge plus an updated topology hash) fails; a change needs a new version + pin.
FROZEN_VERSIONS = {
    'system-v1': 'df96f4a49c96a8a138ff0539ed0e78dfa69895186bc3725a0f9d057b794c91f8',
}


def entry_sha256(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def closure(topology, service):
    seen, stack = set(), list(topology.get(service, ()))
    while stack:
        item = stack.pop()
        if item not in seen:
            seen.add(item)
            stack.extend(topology.get(item, ()))
    return seen
SERVICE_NAME = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*-service')


def service_names(paths, ignored):
    """Every string list and every scalar string in a contract that looks like a service name.

    Selection is by shape, not by membership in a known list, so an enumeration whose
    names are all misspelt (e.g. ["paymant-service"]) is still checked (Codex review).
    """
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


def registry_errors(registry, contract_paths, routes, topology, pins):
    versions = registry['versions']
    current = registry['current']
    errors = []
    if current not in versions:
        return ['current system version is not declared']
    if set(versions[current]['services']) != set(routes):
        errors.append(f'{current} services differ from BUSINESS_ROUTES {sorted(routes)}')
    named = {name for name in topology if name.endswith('-service')}
    if named != set(routes):
        errors.append(f'topology services {sorted(named)} differ from BUSINESS_ROUTES')
    for service, downstream in topology.items():
        if not all(item in topology for item in downstream):
            errors.append(f'{service} depends on an undeclared component')
    def complete(block, keys):
        return isinstance(block, dict) and all(block.get(key) not in (None, '') for key in keys)
    for version, entry in versions.items():
        if pins.get(version) != entry_sha256(entry):
            errors.append(f'{version} differs from its frozen pin; declare a new system version instead')
        if not complete(entry.get('run_identity'), RUN_IDENTITY):
            errors.append(f'{version} lacks run_identity {list(RUN_IDENTITY)}')
        if not complete(entry.get('capture_identity'), CAPTURE_IDENTITY):
            errors.append(f'{version} lacks capture_identity {list(CAPTURE_IDENTITY)}')
        per_service = entry.get('service_identity', {})
        for service in entry['services']:
            if not complete(per_service.get(service), ('route', 'code_tree', 'workload_kinds')) or any(
                    key not in per_service[service] for key in SERVICE_IDENTITY):
                errors.append(f'{version} lacks service_identity for {service}')
    for service in versions[current]['services']:
        declared = versions[current].get('service_identity', {}).get(service, {})
        if set(declared.get('downstream', ())) != closure(topology, service):
            errors.append(f'{current} {service} downstream differs from the topology call path')
        if isinstance(routes.get(service), tuple) and declared.get('route') != ' '.join(routes[service]):
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


def test_repository_contracts_routes_and_topology_agree():
    topology = yaml.safe_load((ROOT / 'configs/topology.yaml').read_text())['dependencies']
    paths = sorted((ROOT / 'contracts').glob('*.json'))
    assert registry_errors(REGISTRY, paths, BUSINESS_ROUTES, topology, FROZEN_VERSIONS) == []
    # Changing the topology means a new system version, never a silent edit of the current one.
    current = REGISTRY['versions'][REGISTRY['current']]['capture_identity']
    assert current['topology_sha256'] == hashlib.sha256((ROOT / 'configs/topology.yaml').read_bytes()).hexdigest()
    # The same holds for the run identity: a new SLO policy, scoring or source profile invalidates
    # the current version's evidence for every service (ADR-017 1a), so it needs a new version.
    run = REGISTRY['versions'][REGISTRY['current']]['run_identity']
    plan = json.loads((ROOT / 'configs/alibaba_local_evaluation.json').read_text())
    assert run['slo_health_policy_sha256'] == stable_id(json.loads((ROOT / 'configs/slo_health_policy.json').read_text()))
    assert run['scoring_sha256'] == stable_id(plan['scoring'])
    assert run['source_profile'] == plan['source_profile']


V1 = ['checkout-service', 'inventory-service', 'payment-service']
V2 = V1 + ['fraud-service']
TOPOLOGY_V2 = {'checkout-service': ['payment-service', 'inventory-service'], 'payment-service': ['fraud-service', 'rds'],
               'inventory-service': ['rds'], 'fraud-service': ['rds'], 'rds': []}


def contracts(tmp_path, files):
    for name, services in files.items():
        body = {'properties': {'service': {'enum': services}}} if isinstance(services, list) else services
        (tmp_path / name).write_text(json.dumps(body))
    return sorted(tmp_path.glob('*.json'))


TOPOLOGY_V1 = {'checkout-service': ['payment-service', 'inventory-service'], 'payment-service': ['rds'],
               'inventory-service': ['rds'], 'rds': []}


def version(services, topology):
    return {'services': services, 'run_identity': dict.fromkeys(RUN_IDENTITY, 'fixture'),
            'capture_identity': dict.fromkeys(CAPTURE_IDENTITY, 'fixture'),
            'service_identity': {s: {'route': 'GET /' + s, 'downstream': sorted(closure(topology, s)),
                                     'shared_infrastructure': [], 'code_tree': 'tree-' + s, 'workload_kinds': ['healthy']}
                                 for s in services}}


def registry(frozen):
    versions = {'system-v1': version(V1, TOPOLOGY_V1), 'system-v2': version(V2, TOPOLOGY_V2)}
    return {'current': 'system-v2', 'frozen_contracts': frozen, 'versions': versions}


def pins(declared):
    return {name: entry_sha256(entry) for name, entry in declared['versions'].items()}


def test_adding_a_service_in_a_new_contract_version_keeps_the_old_contract_valid(tmp_path):
    # Codex review 2026-11-10: the first guard failed this correct migration.
    paths = contracts(tmp_path, {'health-signal.schema.json': V1, 'health-signal-v2.schema.json': V2})
    declared = registry({'health-signal.schema.json': 'system-v1'})
    errors = registry_errors(declared, paths, dict.fromkeys(V2), TOPOLOGY_V2, pins(declared))
    assert errors == []


@pytest.mark.parametrize('case', ['current-contract-not-updated', 'old-contract-not-pinned', 'pin-to-current',
                                  'pin-missing-file', 'routes-not-updated', 'topology-not-updated',
                                  'all-names-misspelt', 'one-name-misspelt', 'misspelt-const',
                                  'new-version-without-run-identity', 'service-without-identity',
                                  'v1-edited-in-place', 'new-version-not-pinned', 'downstream-stale'])
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
        topology['payment-service'] = ['rds']
    declared = registry(frozen)
    pinned = pins(declared)
    if case == 'new-version-without-run-identity':
        declared['versions']['system-v2']['run_identity'].pop('slo_health_policy_sha256')
        pinned = pins(declared)
    if case == 'service-without-identity':
        declared['versions']['system-v2']['service_identity'].pop('fraud-service')
        pinned = pins(declared)
    if case == 'v1-edited-in-place':
        # Codex re-review: add Checkout -> RDS and update the hash inside system-v1; the pin must catch it.
        declared['versions']['system-v1']['capture_identity']['topology_sha256'] = 'edited'
        declared['versions']['system-v1']['service_identity']['checkout-service']['downstream'].append('rds-direct')
    if case == 'new-version-not-pinned':
        pinned.pop('system-v2')
    if case == 'downstream-stale':
        topology['checkout-service'] = ['payment-service', 'inventory-service', 'rds']
        topology['inventory-service'] = []
    assert registry_errors(declared, contracts(tmp_path, files), routes, topology, pinned)


def test_editing_the_repository_system_v1_in_place_fails_the_pin():
    edited = json.loads(json.dumps(REGISTRY))
    v1 = edited['versions']['system-v1']
    v1['service_identity']['checkout-service']['downstream'] = ['inventory-service', 'payment-service', 'rds']
    v1['capture_identity']['topology_sha256'] = '0' * 64
    assert entry_sha256(v1) != FROZEN_VERSIONS['system-v1']
