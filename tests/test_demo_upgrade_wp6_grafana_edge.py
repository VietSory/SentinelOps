"""WP6 acceptance (frozen): early-warning Grafana dashboard and the /ready edge allowlist.

Spec: docs/delivery/DEMO_UPGRADE_SPEC.md, WP6 (R6.1, R6.2, A6.1, A6.2).
"""
import json
from pathlib import Path
import re
import subprocess

import pytest

from scripts.check_grafana_dashboards import (assert_legend_present, assert_missing_data_is_not_healthy,
    assert_provisioned_read_only, assert_queries_resolve, assert_single_axis, dashboards,
    exported_metric_names, recording_rule_names)

ROOT = Path(__file__).resolve().parents[1]
RISK = 'sentinelops-risk.json'
EXPECTED = {
    'timeseries': ['sentinelops_risk_score', 'sentinelops_risk_threshold'],
    'state-timeline': ['sentinelops_risk_state == 1'],
    'stat': ['sum(sentinelops_risk_episode_active)'],
}


def test_risk_dashboard_panels_and_queries():
    board = dashboards()[RISK]
    assert board['uid'] == 'sentinelops-risk' and board['title'] == 'SentinelOps — Cảnh báo sớm'
    assert 'không phải sự cố' in board['description'].lower()
    by_type = {}
    for panel in board['panels']:
        for target in panel.get('targets', []):
            by_type.setdefault(panel['type'], []).append(target['expr'])
    for kind, expressions in EXPECTED.items():
        for expression in expressions:
            assert any(expression in e for e in by_type.get(kind, [])), (kind, expression)
    annotations = board['annotations']['list']
    assert any(a.get('expr') == 'changes(sentinelops_risk_episode_started_unixtime[2m]) > 0' for a in annotations)


def test_risk_dashboard_passes_the_console_guards():
    only = {RISK: dashboards()[RISK]}
    known = exported_metric_names() | recording_rule_names()
    for name in ('sentinelops_risk_score', 'sentinelops_risk_state', 'sentinelops_risk_episode_active',
                 'sentinelops_risk_episode_started_unixtime', 'sentinelops_risk_last_scored_unixtime',
                 'sentinelops_risk_threshold', 'sentinelops_notifications_sent_total'):
        assert name in known
    assert assert_queries_resolve(only, known)
    assert assert_missing_data_is_not_healthy(only)
    assert assert_single_axis(only) and assert_legend_present(only)
    assert assert_provisioned_read_only(only)


EDGE = (ROOT / 'infra' / 'aws' / 'edge.tf').read_text()


def cloudfront_allows(uri):
    code = re.search(r'code\s+=\s+<<-JS\n(.*?)\n\s*JS\n', EDGE, re.S).group(1).replace('%%', '%')
    script = code + f'\nconsole.log(JSON.stringify(handler({{request:{{uri:{json.dumps(uri)},method:"GET"}}}})));'
    result = json.loads(subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True).stdout)
    return result.get('statusCode') != 403


def test_edge_allows_exactly_the_readiness_routes():
    api_paths = re.search(r'api\s+=\s+(\[[^\]]*\])', EDGE).group(1)
    assert json.loads(api_paths) == ['/ui', '/ui/*', '/v1/*', '/ready', '/ready/incidents']
    for allowed in ('/ready', '/ready/incidents', '/ui/', '/v1/incidents', '/grafana/'):
        assert cloudfront_allows(allowed), allowed
    for blocked in ('/readyx', '/ready/actions', '/ready/', '/docs', '/openapi.json', '/metrics', '/v1/internal/x'):
        assert not cloudfront_allows(blocked), blocked
