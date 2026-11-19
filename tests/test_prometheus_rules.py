"""Guards for the operator Prometheus rule wiring; no stack or promtool needed."""
from __future__ import annotations

import json

import pytest
import yaml

from scripts.check_prometheus_rules import (
    CONFIG, PROJECT, RULES, TESTS, mounted_rule_files, prometheus_image, write_report)

COMPOSE = yaml.safe_load((PROJECT/'docker-compose.yml').read_text())
PROMETHEUS_CONFIG = yaml.safe_load(CONFIG.read_text())


def compose_with(volumes):
    return {'services': {'prometheus': {'image': 'prom/prometheus:v3.5.0', 'volumes': volumes}}}


def test_repository_wiring_resolves_every_declared_rule_file():
    resolved = mounted_rule_files(COMPOSE, PROMETHEUS_CONFIG)
    assert resolved
    for target, source in resolved.items():
        assert target.startswith('/etc/prometheus/')
        assert source.is_file() and source.is_relative_to(RULES)


def test_pinned_prometheus_image_is_required():
    assert prometheus_image(COMPOSE).startswith('prom/prometheus:v')
    for image in ('prom/prometheus:latest', 'prom/prometheus', 42):
        with pytest.raises(ValueError):
            prometheus_image({'services': {'prometheus': {'image': image}}})


def test_referenced_but_unmounted_rule_file_is_rejected():
    """Prometheus would fail to start, so this must not pass validation."""
    compose = compose_with(['./observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro'])
    with pytest.raises(ValueError, match='not bind-mounted'):
        mounted_rule_files(compose, {'rule_files': ['/etc/prometheus/rules/sentinelops-workers.yml']})


def test_writable_configuration_bind_is_rejected():
    compose = compose_with(['./observability/rules/sentinelops-workers.yml:/etc/prometheus/rules/sentinelops-workers.yml'])
    with pytest.raises(ValueError, match='read-only'):
        mounted_rule_files(compose, {'rule_files': ['/etc/prometheus/rules/sentinelops-workers.yml']})


def test_glob_rule_reference_is_rejected():
    """A glob would load files the deployment lock never hashed."""
    compose = compose_with(['./observability/rules/sentinelops-workers.yml:/etc/prometheus/rules/sentinelops-workers.yml:ro'])
    with pytest.raises(ValueError, match='explicit files'):
        mounted_rule_files(compose, {'rule_files': ['/etc/prometheus/rules/*.yml']})


def test_absent_rule_configuration_is_rejected():
    with pytest.raises(ValueError, match='no Prometheus rule files'):
        mounted_rule_files(COMPOSE, {})


def test_report_creates_its_directory_and_never_overwrites(tmp_path):
    """CI passes a nested --output path that does not exist yet."""
    output = tmp_path/'observability'/'ci-rules.json'
    write_report({'status': 'PASS'}, output)
    assert json.loads(output.read_text())['status'] == 'PASS'
    with pytest.raises(ValueError, match='must be new'):
        write_report({'status': 'PASS'}, output)


def test_every_rule_file_has_unit_tests():
    covered = set()
    for path in TESTS.glob('*.test.yml'):
        for entry in yaml.safe_load(path.read_text())['rule_files']:
            covered.add((path.parent/entry).resolve())
    declared = set(mounted_rule_files(COMPOSE, PROMETHEUS_CONFIG).values())
    assert declared and declared <= covered


def test_alerts_do_not_depend_on_other_rule_groups():
    """An alert reading a recording rule also depends on evaluation scheduling."""
    recorded = set()
    alerts = []
    for path in sorted(RULES.glob('*.yml')):
        for group in yaml.safe_load(path.read_text())['groups']:
            for rule in group['rules']:
                if 'record' in rule:
                    recorded.add(rule['record'])
                else:
                    alerts.append(rule)
    assert recorded and alerts
    for rule in alerts:
        for name in recorded:
            assert name not in rule['expr'], rule['alert']+' must inline '+name


def test_alerts_declare_severity_and_never_claim_authority():
    forbidden = ('root cause', 'confirmed', 'remediat', 'rollback', 'auto-action', 'healthy and')
    for path in sorted(RULES.glob('*.yml')):
        for group in yaml.safe_load(path.read_text())['groups']:
            for rule in group['rules']:
                if 'alert' not in rule:
                    continue
                assert rule['labels']['severity'] in ('info', 'warning', 'critical')
                text = (rule['annotations']['summary']+' '+rule['annotations']['description']).lower()
                for phrase in forbidden:
                    assert phrase not in text, rule['alert']+' must not imply '+phrase


def test_model_unavailability_is_not_alerted():
    """No approved model exists, so /ready 503 is the expected steady state."""
    for path in sorted(RULES.glob('*.yml')):
        document = yaml.safe_load(path.read_text())
        for group in document['groups']:
            for rule in group['rules']:
                assert 'sentinelops_model' not in rule.get('expr', '')
                if 'alert' in rule:
                    assert 'ready' not in rule['alert'].lower() or 'worker' in rule['alert'].lower()


def _business_rules():
    groups = yaml.safe_load((PROJECT / 'observability/rules/sentinelops-business.yml').read_text())['groups']
    return {rule['record']: rule['expr'] for group in groups for rule in group['rules']}


def test_business_breach_thresholds_match_the_health_policy():
    # The incident dashboards must agree with the health worker's own thresholds.
    policy = json.loads((PROJECT / 'configs/slo_health_policy.json').read_text())
    rules = _business_rules()
    latency = float(rules['sentinelops:slo_latency_breach'].rsplit('> bool', 1)[1])
    error = float(rules['sentinelops:slo_error_breach'].rsplit('> bool', 1)[1])
    assert latency == policy['latency_ms']
    assert error == policy['error_rate']


def test_every_declared_rule_file_ships_in_the_kubernetes_rules_configmap():
    # Prometheus treats rule_files as globs: a file missing from the ConfigMap loads
    # nothing and raises no error, which silently empties every dashboard on it.
    from scripts.kubernetes_testbed import render
    from tests.test_kubernetes_testbed import eks_images, images
    declared = {path.rsplit('/', 1)[1] for path in PROMETHEUS_CONFIG['rule_files']}
    for documents in (render(images()), render(eks_images(), target='eks')):
        rules = next(d for d in documents if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'prometheus-rules')
        assert declared <= set(rules['data']), declared - set(rules['data'])
        for name in declared:
            assert rules['data'][name] == (RULES / name).read_text()


def test_incident_status_panels_read_the_tested_status_rule():
    # The status semantics live in one promtool-tested rule; panels must not re-derive them.
    dashboard = json.loads((PROJECT / 'observability/grafana/dashboards/sentinelops-incidents.json').read_text())
    status = [p for p in dashboard['panels'] if p.get('title', '').endswith('trạng thái SLO')]
    assert len(status) == 3
    for panel in status:
        service = panel['targets'][0]['legendFormat']
        assert panel['targets'][0]['expr'] == f'sentinelops:slo_status{{service="{service}"}}'
    assert 'sentinelops:slo_status' in _business_rules()
