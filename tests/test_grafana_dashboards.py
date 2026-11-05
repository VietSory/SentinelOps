"""Guards for the provisioned Grafana console. No Grafana or Prometheus needed.

Each negative test mutates a copy of the real dashboard and asserts the guard
rejects it, so a guard cannot silently stop guarding.
"""
from __future__ import annotations

import copy
import json

import pytest
import yaml

from scripts.check_grafana_dashboards import (
    DASHBOARDS, GRAFANA, PROJECT, assert_console_is_not_production_auth, assert_legend_present,
    assert_missing_data_is_not_healthy, assert_provisioned_read_only, assert_queries_resolve,
    assert_single_axis, assert_source_availability_is_guarded, dashboards, exported_metric_names,
    recording_rule_names)

KNOWN = exported_metric_names() | recording_rule_names()
NAME = 'sentinelops-workers.json'


@pytest.fixture
def board():
    return copy.deepcopy(dashboards())


def panel(board, title):
    return next(item for item in board[NAME]['panels'] if item['title'] == title)


def test_real_console_satisfies_every_guard():
    all_dashboards = dashboards()
    checks = (assert_queries_resolve(all_dashboards, KNOWN)
              + assert_source_availability_is_guarded(all_dashboards)
              + assert_missing_data_is_not_healthy(all_dashboards)
              + assert_single_axis(all_dashboards) + assert_legend_present(all_dashboards)
              + assert_provisioned_read_only(all_dashboards) + assert_console_is_not_production_auth())
    assert len(checks) == 9


def test_dashboard_declares_its_limits():
    description = dashboards()[NAME]['description'].lower()
    for phrase in ('not business', 'not model quality', 'not rca accuracy'):
        assert phrase in description
    scope = panel(dashboards(), 'Scope')['options']['content'].lower()
    assert 'missing data is unknown, never healthy' in scope
    assert 'no panel grants model activation or remediation authority' in scope


def test_misspelled_metric_is_rejected(board):
    """A typo would render an empty panel that silently looks like no problem."""
    panel(board, 'Replica readiness')['targets'][0]['expr'] = 'sentinelops_worker_redy'
    with pytest.raises(ValueError, match='unknown series'):
        assert_queries_resolve(board, KNOWN)


def test_unknown_template_variable_query_is_rejected(board):
    board[NAME]['templating']['list'][0]['query'] = 'label_values(sentinelops_bogus_metric, environment)'
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
    board[NAME]['panels'] = [item for item in board[NAME]['panels']
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


def test_editable_dashboard_is_rejected(board):
    board[NAME]['editable'] = True
    with pytest.raises(ValueError, match='not editable'):
        assert_provisioned_read_only(board)


def test_foreign_datasource_is_rejected(board):
    panel(board, 'Replica readiness')['datasource'] = {'type': 'prometheus', 'uid': 'somewhere-else'}
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
    assert len(binds) == 3
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
    for path in DASHBOARDS.glob('*.json'):
        document = json.loads(path.read_text())
        assert document['uid'] and document['title']
    datasource = yaml.safe_load((GRAFANA/'provisioning/datasources/prometheus.yaml').read_text())
    assert len(datasource['datasources']) == 1
