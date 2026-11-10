"""ADR-017 decision 5: BUSINESS_ROUTES is the single source of business service names.

Adding a service must fail here until every schema enumeration and the RCA topology
agree, so no place silently keeps the old 3-service list.
"""
import json
from pathlib import Path

import yaml

from sentinelops.telemetry.core import BUSINESS_ROUTES

ROOT = Path(__file__).resolve().parents[1]
SERVICES = set(BUSINESS_ROUTES)


def service_lists():
    """Every string list in a contract that names at least one business service."""
    for path in sorted((ROOT / 'contracts').glob('*.json')):
        def walk(node, pointer):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield from walk(value, pointer + '/' + key)
            elif isinstance(node, list):
                if node and all(isinstance(item, str) for item in node) and SERVICES & set(node):
                    yield path.name + pointer, node
                for index, value in enumerate(node):
                    yield from walk(value, f'{pointer}[{index}]')
        yield from walk(json.loads(path.read_text()), '')


def test_every_schema_service_list_names_all_business_services():
    found = list(service_lists())
    assert found, 'expected schema enumerations of business services'
    for where, values in found:
        named = {value for value in values if value.endswith('-service')}
        assert named == SERVICES, f'{where} lists {sorted(named)}, BUSINESS_ROUTES has {sorted(SERVICES)}'


def test_rca_topology_covers_exactly_the_business_services():
    topology = yaml.safe_load((ROOT / 'configs/topology.yaml').read_text())['dependencies']
    named = {name for name in topology if name.endswith('-service')}
    assert named == SERVICES
    for service, downstream in topology.items():
        assert all(item in topology for item in downstream), f'{service} depends on an undeclared component'
