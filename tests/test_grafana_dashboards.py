"""Guards for the provisioned Grafana console. No Grafana or Prometheus needed.

Each negative test mutates a copy of the real dashboard and asserts the guard
rejects it, so a guard cannot silently stop guarding.
"""
from __future__ import annotations

import copy
from io import BytesIO
import json
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from scripts import check_grafana_dashboards as checker
from scripts.check_grafana_dashboards import (
    DASHBOARDS, GRAFANA, PROJECT, assert_console_is_not_production_auth, assert_legend_present,
    assert_missing_data_is_not_healthy, assert_provisioned_read_only, assert_queries_resolve,
    assert_single_axis, assert_source_availability_is_guarded, dashboards, exported_metric_names,
    recording_rule_names, assert_oom_observations_are_guarded)

KNOWN = exported_metric_names() | recording_rule_names()
WORKERS = 'sentinelops-workers.json'
OOM = 'sentinelops-oom.json'
DASHBOARD_UIDS = {
    'sentinelops-incidents.json': 'sentinelops-incidents',
    'sentinelops-slo.json': 'sentinelops-slo',
    WORKERS: 'sentinelops-workers',
    OOM: 'sentinelops-oom',
    'sentinelops-risk.json': 'sentinelops-risk',
}


@pytest.fixture
def board():
    return copy.deepcopy(dashboards())


def panel(board, title):
    return next(item for item in board[WORKERS]['panels'] if item['title'] == title)


def test_real_console_satisfies_every_guard():
    all_dashboards = dashboards()
    assert set(all_dashboards) == set(DASHBOARD_UIDS)
    checks = (assert_queries_resolve(all_dashboards, KNOWN)
              + assert_source_availability_is_guarded(all_dashboards)
              + assert_oom_observations_are_guarded(all_dashboards)
              + assert_missing_data_is_not_healthy(all_dashboards)
              + assert_single_axis(all_dashboards) + assert_legend_present(all_dashboards)
              + assert_provisioned_read_only(all_dashboards) + assert_console_is_not_production_auth())
    # Guards cover all four dashboards; provisioning emits three checks.
    assert len(checks) == 7 + 3


def test_dashboard_declares_its_limits():
    description = dashboards()[WORKERS]['description'].lower()
    for phrase in ('not business', 'not model quality', 'not rca accuracy'):
        assert phrase in description
    scope = panel(dashboards(), 'Scope')['options']['content'].lower()
    assert 'missing data is unknown, never healthy' in scope
    assert 'no panel grants model activation or remediation authority' in scope


def oom_panel(board, id):
    return next(item for item in board[OOM]['panels'] if item['id'] == id)


def test_oom_metric_catalog_and_scope_are_real_and_explicit():
    from sentinelops.remediation.pod_memory_metrics import EXPORTED_METRIC_NAMES, FRESH_SECONDS
    oom = dashboards()[OOM]
    assert FRESH_SECONDS == 20
    metrics = set().union(*(checker.referenced_metrics(expr) for _, expr in checker.queries(oom)))
    assert metrics == set(EXPORTED_METRIC_NAMES)
    scope = oom_panel(dashboards(), 1)['options']['content']
    for phrase in ('không phải lượng RAM đang dùng', '256 MiB → 64 MiB', 'UNKNOWN', '/ui/'):
        assert phrase in scope
    assert not any('sentinelops_http_' in expr for _, expr in checker.queries(oom))
    assert oom_panel(dashboards(), 11)['options']['textMode'] == 'name'


@pytest.mark.parametrize('quoted', ['"sentinelops-b-actions"', "'sentinelops-oom-demo'", '`sentinelops_fake_metric`',
                                    '"escaped \\\"sentinelops_fake_metric\\\""'])
def test_metric_names_inside_label_strings_are_not_series(quoted):
    assert checker.referenced_metrics('sentinelops_worker_ready{namespace='+quoted+'}') == {
        'sentinelops_worker_ready'}


def test_quoted_workload_does_not_hide_an_unknown_real_metric(board):
    oom_panel(board, 3)['targets'][0]['expr'] += ' + sentinelops_typo_metric'
    with pytest.raises(ValueError, match='unknown series: sentinelops_typo_metric'):
        assert_queries_resolve(board, KNOWN)


@pytest.mark.parametrize('old,new,error', [
    (' <= 20', ' <= 120', 'stale and future'),
    (' >= 0', ' >= -1', 'stale and future'),
    (checker.OOM_AVAILABLE+checker.OOM_SCOPE+' == 1',
     checker.OOM_AVAILABLE+checker.OOM_SCOPE+' >= 0', 'require observation availability'),
    ('workload="sentinelops-oom-demo"', 'workload="payment"', 'fixed workload scope'),
])
def test_oom_observation_guard_rejects_stale_future_unavailable_or_wrong_target(board, old, new, error):
    target = oom_panel(board, 3)['targets'][0]
    target['expr'] = target['expr'].replace(old, new)
    with pytest.raises(ValueError, match=error):
        assert_oom_observations_are_guarded(board)


def test_oom_missing_data_cannot_be_filled_with_zero(board):
    oom_panel(board, 3)['targets'][0]['expr'] += ' or vector(0)'
    with pytest.raises(ValueError, match='replace missing observations'):
        assert_oom_observations_are_guarded(board)


def test_oom_current_pod_restart_gauge_cannot_be_treated_as_counter(board):
    target = oom_panel(board, 8)['targets'][0]
    metric = 'sentinelops_oom_demo_restarts'+checker.OOM_SCOPE
    target['expr'] = target['expr'].replace(metric, 'increase('+metric+'[1m])')
    with pytest.raises(ValueError, match='current-Pod gauge'):
        assert_oom_observations_are_guarded(board)


@pytest.mark.parametrize('field,value', [('instant', False), ('range', True)])
def test_oom_current_state_cannot_read_a_historical_range(board, field, value):
    oom_panel(board, 3)['targets'][0][field] = value
    with pytest.raises(ValueError, match='current instant'):
        assert_oom_observations_are_guarded(board)


def test_oom_current_state_cannot_carry_forward_a_stale_last_non_null_value(board):
    oom_panel(board, 3)['options']['reduceOptions']['calcs'] = ['lastNotNull']
    with pytest.raises(ValueError, match='last non-null'):
        assert_oom_observations_are_guarded(board)


def test_oom_history_cannot_connect_unknown_gaps(board):
    oom_panel(board, 6)['fieldConfig']['defaults']['custom']['spanNulls'] = True
    with pytest.raises(ValueError, match='missing observations as gaps'):
        assert_oom_observations_are_guarded(board)


def test_oom_dashboard_cannot_drop_all_actual_restart_evidence(board):
    board[OOM]['panels'] = [p for p in board[OOM]['panels'] if p['id'] != 8]
    with pytest.raises(ValueError, match='resource, readiness, restart, event and phase'):
        assert_oom_observations_are_guarded(board)


def test_misspelled_metric_is_rejected(board):
    """A typo would render an empty panel that silently looks like no problem."""
    panel(board, 'Replica readiness')['targets'][0]['expr'] = 'sentinelops_worker_redy'
    with pytest.raises(ValueError, match='unknown series'):
        assert_queries_resolve(board, KNOWN)


def test_unknown_template_variable_query_is_rejected(board):
    board[WORKERS]['templating']['list'][0]['query'] = 'label_values(sentinelops_bogus_metric, environment)'
    with pytest.raises(ValueError, match='unknown series'):
        assert_queries_resolve(board, KNOWN)


def test_unguarded_source_availability_is_rejected(board):
    """Dropping the decision guard would report an undecided replica as down."""
    panel(board, 'Source eligibility for decided replicas')['targets'][0]['expr'] = (
        'sentinelops_worker_source_available{environment="$environment"}')
    with pytest.raises(ValueError, match='without the decision guard'):
        assert_source_availability_is_guarded(board)


def test_explicitly_joined_guard_is_accepted(board):
    panel(board, 'Source eligibility for decided replicas')['targets'][0]['expr'] = (
        'sentinelops_worker_source_available and on(job, environment, worker_instance) '
        '(sentinelops_worker_has_local_decision == 1)')
    assert assert_source_availability_is_guarded(board)


def test_removing_the_source_panel_is_rejected(board):
    board[WORKERS]['panels'] = [item for item in board[WORKERS]['panels']
                             if item['title'] != 'Source eligibility for decided replicas']
    with pytest.raises(ValueError, match='expected a panel showing source eligibility'):
        assert_source_availability_is_guarded(board)


@pytest.mark.parametrize('no_value', ['0', '  0 ', 'OK', 'healthy', 'ready'])
def test_missing_data_presented_as_healthy_is_rejected(board, no_value):
    panel(board, 'Replica readiness')['fieldConfig']['defaults']['noValue'] = no_value
    with pytest.raises(ValueError, match='presents missing data as healthy'):
        assert_missing_data_is_not_healthy(board)


def test_state_panel_without_a_no_value_is_rejected(board):
    del panel(board, 'Replica readiness')['fieldConfig']['defaults']['noValue']
    with pytest.raises(ValueError, match='must state what absent data means'):
        assert_missing_data_is_not_healthy(board)


def test_stale_panel_reads_zero_only_when_replicas_are_observed():
    """`or (count(...) * 0)` keeps zero meaning observed-and-fresh."""
    expression = panel(dashboards(), 'Replicas with a stale heartbeat')['targets'][0]['expr']
    assert '* 0' in expression and ' or ' in expression
    no_value = panel(dashboards(), 'Replicas with a stale heartbeat')['fieldConfig']['defaults']['noValue']
    assert 'UNKNOWN' in no_value


def test_value_mapped_to_colour_without_text_is_rejected(board):
    mappings = panel(board, 'Replica readiness')['fieldConfig']['defaults']['mappings']
    mappings[0]['options']['1'] = {'color': 'green', 'index': 0}
    with pytest.raises(ValueError, match='colour without text'):
        assert_missing_data_is_not_healthy(board)


def test_second_y_axis_is_rejected(board):
    panel(board, 'Committed windows')['fieldConfig']['overrides'] = [
        {'matcher': {'id': 'byName', 'options': 'x'},
         'properties': [{'id': 'custom.axisPlacement', 'value': 'right'}]}]
    with pytest.raises(ValueError, match='second y-axis'):
        assert_single_axis(board)


def test_per_series_unit_override_is_rejected(board):
    panel(board, 'Committed windows')['fieldConfig']['overrides'] = [
        {'matcher': {'id': 'byName', 'options': 'x'}, 'properties': [{'id': 'unit', 'value': 's'}]}]
    with pytest.raises(ValueError, match='two measures on one axis'):
        assert_single_axis(board)


def test_hidden_legend_is_rejected(board):
    panel(board, 'Committed windows')['options']['legend']['showLegend'] = False
    with pytest.raises(ValueError, match='legend'):
        assert_legend_present(board)


@pytest.mark.parametrize('name', DASHBOARD_UIDS)
def test_editable_dashboard_is_rejected(board, name):
    board[name]['editable'] = True
    with pytest.raises(ValueError, match='not editable'):
        assert_provisioned_read_only(board)


@pytest.mark.parametrize('name', DASHBOARD_UIDS)
def test_foreign_datasource_is_rejected(board, name):
    target = next(item for item in board[name]['panels'] if item.get('targets'))
    target['datasource'] = {'type': 'prometheus', 'uid': 'somewhere-else'}
    with pytest.raises(ValueError, match='points at datasource'):
        assert_provisioned_read_only(board)


def test_console_is_loopback_only_and_pinned():
    compose = yaml.safe_load((PROJECT/'docker-compose.yml').read_text())
    service = compose['services']['grafana']
    assert service['image'].startswith('grafana/grafana:') and not service['image'].endswith(':latest')
    assert all(str(port).startswith('127.0.0.1:') for port in service['ports'])
    # Anonymous viewing is a sandbox convenience, never production auth.
    assert service['environment']['GF_AUTH_ANONYMOUS_ORG_ROLE'] == 'Viewer'
    assert str(service['environment']['GF_USERS_ALLOW_SIGN_UP']).lower() == 'false'


def test_provisioning_binds_are_individual_read_only_files():
    compose = yaml.safe_load((PROJECT/'docker-compose.yml').read_text())
    binds = [mount for mount in compose['services']['grafana']['volumes'] if mount.startswith('./')]
    expected = {
        './observability/grafana/provisioning/datasources/prometheus.yaml:/etc/grafana/provisioning/datasources/prometheus.yaml:ro',
        './observability/grafana/provisioning/dashboards/sentinelops.yaml:/etc/grafana/provisioning/dashboards/sentinelops.yaml:ro',
        *(f'./observability/grafana/dashboards/{name}:/var/lib/grafana/dashboards/{name}:ro'
          for name in DASHBOARD_UIDS),
    }
    assert len(binds) == 7  # +1 risk dashboard (WP6)
    assert set(binds) == expected
    for mount in binds:
        source, _, mode = mount.split(':')
        assert mode == 'ro'
        # A directory bind would let the deployment lock miss a file.
        assert (PROJECT/source).is_file()


def test_grafana_port_is_reserved_in_the_lane_b_launcher():
    from scripts.lane_b import PORTS
    assert PORTS['grafana'] == {13000: 3000}
    published = [port for mapping in PORTS.values() for port in mapping]
    assert len(set(published)) == len(published)


def test_dashboard_file_is_valid_json_and_uniquely_identified():
    paths = sorted(DASHBOARDS.glob('*.json'))
    assert {path.name for path in paths} == set(DASHBOARD_UIDS)
    seen_uids = set()
    for path in paths:
        document = json.loads(path.read_text())
        assert document['uid'] == DASHBOARD_UIDS[path.name]
        assert document['uid'] not in seen_uids
        seen_uids.add(document['uid'])
        assert document['title']
    datasource = yaml.safe_load((GRAFANA/'provisioning/datasources/prometheus.yaml').read_text())
    assert len(datasource['datasources']) == 1


def test_slo_dashboard_p95_thresholds_match_the_health_policy():
    policy = json.loads((PROJECT/'configs/slo_health_policy.json').read_text())
    slo = dashboards()['sentinelops-slo.json']
    p95_panels = [item for item in slo['panels'] if any(
        'http_latency_p95' in target.get('expr', '') for target in item.get('targets', []))]
    assert len(p95_panels) == 6
    for item in p95_panels:
        values = [step['value'] for step in item['fieldConfig']['defaults']['thresholds']['steps']
                  if step['value'] is not None]
        assert values[-1] == policy['latency_ms'], item['title']
        assert '1000 ms' not in (item['title'] + ' ' + item.get('description', ''))
    assert 'ngưỡng p95 latency 750 ms' in slo['description']
    # Success-rate targets remain independent from the incident latency policy.
    success = [item for item in slo['panels'] if 'Success Rate' in item.get('title', '')]
    assert len(success) == 3
    assert all([step['value'] for step in item['fieldConfig']['defaults']['thresholds']['steps'][1:]] == [.995,.997]
               for item in success)


def running_grafana(monkeypatch, query_response=None):
    boards, evaluated = dashboards(), []
    by_uid = {board['uid']: board for board in boards.values()}
    def urlopen(url, timeout):
        assert timeout == 10
        parsed = urlsplit(url)
        if parsed.path == '/api/health':
            body = {'database': 'ok', 'version': 'fixture'}
        elif parsed.path.startswith('/api/dashboards/uid/'):
            body = {'dashboard': by_uid[parsed.path.rsplit('/', 1)[1]], 'meta': {'provisioned': True}}
        elif parsed.path == '/api/datasources':
            body = [{'uid': checker.DATASOURCE_UID}]
        else:
            assert parsed.path == '/api/datasources/proxy/uid/sentinelops-prometheus/api/v1/query'
            expression, = parse_qs(parsed.query)['query']
            assert '$' not in expression, 'the datasource proxy requires interpolated PromQL'
            evaluated.append(expression)
            body = query_response or {'status': 'success', 'data': {'resultType': 'vector', 'result': []}}
        return BytesIO(json.dumps(body).encode())
    monkeypatch.setattr(checker.urllib.request, 'urlopen', urlopen)
    return boards, evaluated


def test_runtime_verification_interpolates_every_dashboard_query_without_mutating_it(monkeypatch):
    boards, evaluated = running_grafana(monkeypatch)
    original = copy.deepcopy(boards)
    result = checker.verify_running('http://grafana.invalid', boards)
    assert result['expressions_evaluated_on_prometheus'] == len(evaluated) == 66  # +4 risk dashboard queries (WP6)
    assert sum(len(checker.queries(board)) for board in boards.values()) == len(evaluated)
    assert any('sentinelops_oom_demo_memory_limit_bytes' in expression for expression in evaluated)
    for metric in ('sentinelops_incident_groups_total', 'sentinelops_rca_abstentions_total'):
        assert f'sum by (environment) (increase({metric}[1h]))' in evaluated
    assert boards == original


def test_runtime_verification_still_rejects_a_failed_prometheus_evaluation(monkeypatch):
    boards, _ = running_grafana(monkeypatch, {'status': 'error', 'error': 'invalid PromQL'})
    with pytest.raises(ValueError, match='failed evaluation'):
        checker.verify_running('http://grafana.invalid', boards)
