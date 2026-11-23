"""WP0 acceptance (frozen): live demo checkers.

Spec: docs/delivery/DEMO_UPGRADE_SPEC.md, WP0 (R0.1, R0.2, A0.1-A0.3).
"""
from copy import deepcopy
import json

import pytest

from scripts import check_demo_round, check_risk_quiet


def receipt(round_number=1, invocation='run-1'):
    return {
        'receipt_version': 'runbook-demo-round/v2', 'runner_invocation_id': invocation, 'round': round_number,
        'scenario': 'gradual-regression',
        'deploy_started_at': '2026-12-01T10:00:00Z', 'change_observed_at': '2026-12-01T10:00:20Z',
        'first_breach_at': '2026-12-01T10:07:00Z', 'incident_created_at': '2026-12-01T10:09:30Z',
        'proposal_created_at': '2026-12-01T10:10:00Z', 'approved_at': '2026-12-01T10:12:00Z',
        'recovered_at': '2026-12-01T10:20:00Z',
        'warning': {'service': 'payment-service', 'episode_started_at': '2026-12-01T10:03:00Z'},
        'lead_seconds': 240.0, 'deploy_to_breach_seconds': 420.0, 'rca_top1': 'payment-service',
        'proposal': {'proposal_version': 'runbook-proposal/v2', 'approval_policy': 'pending-until-decision/v1',
                     'proposal_id': 'a' * 64},
        'action': {'phase': 'recovered', 'telemetry_verified': True}, 'dispatch_count': 1,
        'notifications': {'early_warning': 1, 'proposal_pending': 1, 'outcome': 1},
        'image_params': {'start_delay_s': 60, 'ramp_s': 600, 'max_ms': 1200},
    }


def write(tmp_path, values):
    paths = []
    for index, value in enumerate(values):
        path = tmp_path / f'round-{index}.json'
        path.write_text(json.dumps(value))
        paths.append(str(path))
    return paths


def test_round_checker_passes_a_complete_receipt(tmp_path, capsys):
    assert check_demo_round.main(write(tmp_path, [receipt()])) == 0
    assert check_demo_round.main(write(tmp_path, [receipt(1), receipt(2), receipt(3)]) + ['--require', '3']) == 0


def broken(change):
    value = receipt()
    change(value)
    return value


CASES = {
    'scenario': lambda v: v.update(scenario='abrupt-regression'),
    'proposal_version': lambda v: v['proposal'].update(proposal_version='runbook-proposal/v1'),
    'expires_at': lambda v: v['proposal'].update(expires_at='2026-12-01T10:25:00Z'),
    'warning': lambda v: v.update(warning={'service': 'checkout-service', 'episode_started_at': '2026-12-01T10:03:00Z'}),
    'lead_seconds': lambda v: v['warning'].update(episode_started_at='2026-12-01T10:04:01Z'),
    'deploy_to_breach_seconds': lambda v: v.update(first_breach_at='2026-12-01T10:10:01Z',
                                                   incident_created_at='2026-12-01T10:11:00Z'),
    'rca_top1': lambda v: v.update(rca_top1='checkout-service'),
    'action': lambda v: v['action'].update(phase='escalated'),
    'telemetry_verified': lambda v: v['action'].update(telemetry_verified=False),
    'dispatch_count': lambda v: v.update(dispatch_count=2),
    'notifications': lambda v: v['notifications'].update(outcome=0),
}


@pytest.mark.parametrize('condition', sorted(CASES))
def test_round_checker_names_every_failed_condition(tmp_path, capsys, condition):
    assert check_demo_round.main(write(tmp_path, [broken(CASES[condition])])) == 1
    assert condition in capsys.readouterr().out


def test_round_checker_recomputes_timings_instead_of_trusting_fields(tmp_path, capsys):
    value = receipt()
    value['warning']['episode_started_at'] = '2026-12-01T10:05:00Z'
    value['lead_seconds'] = 999
    assert check_demo_round.main(write(tmp_path, [value])) == 1
    assert 'lead_seconds' in capsys.readouterr().out


@pytest.mark.parametrize('problem', ['count', 'invocation', 'order'])
def test_round_checker_requires_consecutive_rounds_of_one_invocation(tmp_path, capsys, problem):
    values = [receipt(1), receipt(2), receipt(3)]
    if problem == 'count':
        values = values[:2]
    if problem == 'invocation':
        values[1] = receipt(2, 'run-2')
    if problem == 'order':
        values[2] = receipt(4)
    assert check_demo_round.main(write(tmp_path, values) + ['--require', '3']) == 1
    assert 'round' in capsys.readouterr().out


SERVICES = ('checkout-service', 'payment-service', 'inventory-service')


class FakePrometheus:
    def __init__(self, *, episode=0.0, changes=60.0, age=30.0):
        self.queries, self.values = [], {'episode': episode, 'changes': changes, 'age': age}

    def query(self, expression):
        self.queries.append(expression)
        if expression.startswith('max_over_time(sentinelops_risk_episode_active'):
            return self.values['episode']
        if expression.startswith('changes(sentinelops_risk_last_scored_unixtime'):
            return self.values['changes']
        if expression.startswith('time() - sentinelops_risk_last_scored_unixtime'):
            return self.values['age']
        raise AssertionError('unexpected query ' + expression)


def test_quiet_checker_uses_the_exact_queries():
    fake = FakePrometheus()
    assert check_risk_quiet.check(fake, minutes=60) == []
    expected = set()
    for service in SERVICES:
        expected |= {f'max_over_time(sentinelops_risk_episode_active{{service="{service}"}}[60m])',
                     f'changes(sentinelops_risk_last_scored_unixtime{{service="{service}"}}[60m])',
                     f'time() - sentinelops_risk_last_scored_unixtime{{service="{service}"}}'}
    assert set(fake.queries) == expected


@pytest.mark.parametrize('values, failure', [
    ({'episode': 1.0}, 'episode'), ({'changes': 54.0}, 'scored'), ({'age': 181.0}, 'stale'),
    ({'episode': None}, 'missing'),
])
def test_quiet_checker_fails_on_episode_gap_stale_or_missing(values, failure):
    fake = FakePrometheus(**values)
    problems = check_risk_quiet.check(fake, minutes=60)
    assert problems and any(failure in p for p in problems)
