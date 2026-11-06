from copy import deepcopy
import json

import httpx
import pytest
import yaml

from sentinelops.telemetry.prometheus import PrometheusSource


def configuration():
    return {'global': {'scrape_interval': '5s', 'evaluation_interval': '5s'},
            'scrape_configs': [{'job_name': 'otel-collector', 'honor_labels': True,
                               'static_configs': [{'targets': ['otel-collector:8889']}]}]}


def identity(config):
    source = PrometheusSource(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={'status': 'success', 'data': {'yaml': yaml.safe_dump(config)}})))
    try:
        return source.verify_configuration()['configuration_hash']
    finally:
        source.close()


def test_operator_alert_rules_do_not_fork_the_source_stream():
    original = configuration()
    operator = deepcopy(original)
    operator['rule_files'] = ['/etc/prometheus/rules/sentinelops-workers.yml']
    operator['alerting'] = {'alertmanagers': [{'static_configs': [{'targets': ['alerts:9093']}]}]}
    assert identity(original) == identity(operator)


@pytest.mark.parametrize('change', ['target', 'labels', 'relabel', 'global', 'other-job', 'unknown-source-key'])
def test_source_semantics_changes_always_fork_identity(change):
    original = configuration()
    changed = deepcopy(original)
    job = changed['scrape_configs'][0]
    if change == 'target': job['static_configs'][0]['targets'] = ['different:8889']
    elif change == 'labels': job['static_configs'][0]['labels'] = {'environment': 'other'}
    elif change == 'relabel': job['metric_relabel_configs'] = [{'action': 'drop', 'regex': '.*'}]
    elif change == 'global': changed['global']['external_labels'] = {'cluster': 'other'}
    elif change == 'other-job': changed['scrape_configs'].append({'job_name': 'other', 'static_configs': []})
    else: changed['otlp'] = {'promote_resource_attributes': ['service.name']}
    assert identity(original) != identity(changed)


def test_legacy_identity_remains_exact_and_never_relabels_old_streams():
    from sentinelops.telemetry.core import stable_id
    from sentinelops.telemetry.prometheus import matches_source_configuration
    original = configuration()
    changed = {**original, 'rule_files': ['/operator-rules.yml']}
    legacy = stable_id(original)
    assert legacy != identity(original)
    assert matches_source_configuration(original, legacy)
    assert not matches_source_configuration(changed, legacy)
    assert matches_source_configuration(changed, identity(original))


def test_native_evidence_uses_the_same_hash_and_keeps_legacy_exactness():
    import asyncio
    from unittest.mock import AsyncMock
    from sentinelops.telemetry.core import stable_id
    from sentinelops.workers.incident_evidence import NativeIncidentEvidence
    original = configuration()
    changed = {**original, 'rule_files': ['/operator-rules.yml']}

    async def check():
        source = NativeIncidentEvidence('sentinelops-b', prometheus_endpoint='http://127.0.0.1:19090',
                                        jaeger_endpoint='http://127.0.0.1:26686')
        source._owned = AsyncMock(side_effect=lambda service, *args: {'service': service, 'running': True})
        source._http = AsyncMock(return_value=json.dumps({'status': 'success', 'data': {'yaml': yaml.safe_dump(changed)}}).encode())
        selected = {'environment': 'local', 'endpoint_hash': stable_id({'endpoint': source.prometheus_endpoint}),
                    'prometheus_configuration_hash': identity(original)}
        try:
            assert (await source.verify_source(selected))['prometheus']['configuration_available']
            with pytest.raises(ValueError, match='configuration changed'):
                await source.verify_source({**selected, 'prometheus_configuration_hash': stable_id(original)})
            assert (await source.verify_source({**selected, 'prometheus_configuration_hash': stable_id(changed)}))['prometheus']['configuration_available']
        finally:
            await source.close()
    asyncio.run(check())
