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

from sentinelops.telemetry.core import BUSINESS_ROUTES

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / 'configs/system_versions.json').read_text())


# ADR-017 amendment 1 (1a): evidence carries forward only under an identical identity.
EVIDENCE_IDENTITY = ('source_commit', 'model_version', 'bundle_manifest_sha256', 'source_profile',
                     'slo_health_policy_sha256', 'evaluation_plan_sha256', 'topology_sha256')
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
    for version, entry in versions.items():
        identity = entry.get('evidence_identity')
        if not isinstance(identity, dict) or any(not isinstance(identity.get(key), str) or not identity[key]
                                                 for key in EVIDENCE_IDENTITY):
            errors.append(f'{version} lacks the ADR-017 evidence identity {list(EVIDENCE_IDENTITY)}')
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
    assert registry_errors(REGISTRY, paths, BUSINESS_ROUTES, topology) == []
    # Changing the topology means a new system version, never a silent edit of the current one.
    current = REGISTRY['versions'][REGISTRY['current']]['evidence_identity']
    assert current['topology_sha256'] == hashlib.sha256((ROOT / 'configs/topology.yaml').read_bytes()).hexdigest()


V1 = ['checkout-service', 'inventory-service', 'payment-service']
V2 = V1 + ['fraud-service']
TOPOLOGY_V2 = {'checkout-service': ['payment-service', 'inventory-service'], 'payment-service': ['fraud-service', 'rds'],
               'inventory-service': ['rds'], 'fraud-service': ['rds'], 'rds': []}


def contracts(tmp_path, files):
    for name, services in files.items():
        body = {'properties': {'service': {'enum': services}}} if isinstance(services, list) else services
        (tmp_path / name).write_text(json.dumps(body))
    return sorted(tmp_path.glob('*.json'))


IDENTITY = dict.fromkeys(EVIDENCE_IDENTITY, 'fixture')


def registry(frozen):
    return {'current': 'system-v2', 'frozen_contracts': frozen,
            'versions': {'system-v1': {'services': V1, 'evidence_identity': dict(IDENTITY)},
                         'system-v2': {'services': V2, 'evidence_identity': dict(IDENTITY)}}}


def test_adding_a_service_in_a_new_contract_version_keeps_the_old_contract_valid(tmp_path):
    # Codex review 2026-11-10: the first guard failed this correct migration.
    paths = contracts(tmp_path, {'health-signal.schema.json': V1, 'health-signal-v2.schema.json': V2})
    errors = registry_errors(registry({'health-signal.schema.json': 'system-v1'}), paths, dict.fromkeys(V2), TOPOLOGY_V2)
    assert errors == []


@pytest.mark.parametrize('case', ['current-contract-not-updated', 'old-contract-not-pinned', 'pin-to-current',
                                  'pin-missing-file', 'routes-not-updated', 'topology-not-updated',
                                  'all-names-misspelt', 'one-name-misspelt', 'misspelt-const',
                                  'new-version-without-evidence-identity'])
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
    if case == 'new-version-without-evidence-identity':
        declared['versions']['system-v2']['evidence_identity'].pop('slo_health_policy_sha256')
    assert registry_errors(declared, contracts(tmp_path, files), routes, topology)
