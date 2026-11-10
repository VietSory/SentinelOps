"""ADR-017 decision 5: business service names have one source per system version.

`configs/system_versions.json` declares each system version's services. The current
version must equal BUSINESS_ROUTES and the RCA topology. A contract file belongs to the
current version unless it is listed in `frozen_contracts`, which pins an older contract
to the version it was written for. Adding a service in a new contract version therefore
leaves the old contract valid, while forgetting any current enumeration fails here.
"""
import json
from pathlib import Path

import pytest
import yaml

from sentinelops.telemetry.core import BUSINESS_ROUTES

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / 'configs/system_versions.json').read_text())


def service_lists(paths, known):
    """Every string list in a contract that names at least one known business service."""
    for path in paths:
        def walk(node, pointer):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield from walk(value, pointer + '/' + key)
            elif isinstance(node, list):
                if node and all(isinstance(item, str) for item in node) and known & set(node):
                    yield path.name, pointer, node
                for index, value in enumerate(node):
                    yield from walk(value, f'{pointer}[{index}]')
        yield from walk(json.loads(path.read_text()), '')


def registry_errors(registry, contract_paths, routes, topology):
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
    names = {path.name for path in contract_paths}
    for name, version in registry['frozen_contracts'].items():
        if name not in names:
            errors.append(f'frozen contract {name} does not exist')
        if version not in versions or version == current:
            errors.append(f'frozen contract {name} must pin an older declared version, not {version}')
    known = set().union(*(set(v['services']) for v in versions.values()))
    found = 0
    for name, pointer, values in service_lists(contract_paths, known):
        found += 1
        version = registry['frozen_contracts'].get(name, current)
        expected = set(versions[version]['services'])
        listed = {value for value in values if value.endswith('-service')}
        if listed != expected:
            errors.append(f'{name}{pointer} ({version}) lists {sorted(listed)}, expected {sorted(expected)}')
    if not found:
        errors.append('expected schema enumerations of business services')
    return errors


def test_repository_contracts_routes_and_topology_agree():
    topology = yaml.safe_load((ROOT / 'configs/topology.yaml').read_text())['dependencies']
    paths = sorted((ROOT / 'contracts').glob('*.json'))
    assert registry_errors(REGISTRY, paths, BUSINESS_ROUTES, topology) == []


V1 = ['checkout-service', 'inventory-service', 'payment-service']
V2 = V1 + ['fraud-service']
TOPOLOGY_V2 = {'checkout-service': ['payment-service', 'inventory-service'], 'payment-service': ['fraud-service', 'rds'],
               'inventory-service': ['rds'], 'fraud-service': ['rds'], 'rds': []}


def contracts(tmp_path, files):
    for name, services in files.items():
        (tmp_path / name).write_text(json.dumps({'properties': {'service': {'enum': services}}}))
    return sorted(tmp_path.glob('*.json'))


def registry(frozen):
    return {'current': 'system-v2', 'frozen_contracts': frozen,
            'versions': {'system-v1': {'services': V1}, 'system-v2': {'services': V2}}}


def test_adding_a_service_in_a_new_contract_version_keeps_the_old_contract_valid(tmp_path):
    # Codex review 2026-11-10: the first guard failed this correct migration.
    paths = contracts(tmp_path, {'health-signal.schema.json': V1, 'health-signal-v2.schema.json': V2})
    errors = registry_errors(registry({'health-signal.schema.json': 'system-v1'}), paths, dict.fromkeys(V2), TOPOLOGY_V2)
    assert errors == []


@pytest.mark.parametrize('case', ['current-contract-not-updated', 'old-contract-not-pinned', 'pin-to-current',
                                  'pin-missing-file', 'routes-not-updated', 'topology-not-updated'])
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
    if case == 'routes-not-updated':
        routes = dict.fromkeys(V1)
    if case == 'topology-not-updated':
        topology.pop('fraud-service')
        topology['payment-service'] = ['rds']
    assert registry_errors(registry(frozen), contracts(tmp_path, files), routes, topology)
