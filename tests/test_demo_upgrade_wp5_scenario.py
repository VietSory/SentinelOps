"""WP5 acceptance (frozen): gradual Payment regression, console actions, round receipt v2.

Spec: docs/delivery/DEMO_UPGRADE_SPEC.md, WP5 (R5.1-R5.4, A5.1-A5.4).
"""
from datetime import timedelta
import inspect
import json
from pathlib import Path

import pytest

from scripts import check_demo_round
from scripts import payment_regression as regression
from scripts.demo_console import app as console
from scripts.demo_console import gradual_fault
from scripts.run_runbook_demo import round_receipt

ROOT = Path(__file__).resolve().parents[1]
BASE = '123456789012.dkr.ecr.ap-southeast-1.amazonaws.com/sentinelops/application@sha256:' + 'a' * 64


def test_gradual_delay_values():
    delay = regression.gradual_delay_ms
    assert [delay(t, start_delay_s=60, ramp_s=600, max_ms=1200) for t in (0, 59, 60, 360, 660, 1000)] == [
        0, 0, 0, 600, 1200, 1200]
    assert regression.GRADUAL_DEFAULTS == {'start_delay_s': 60, 'ramp_s': 600, 'max_ms': 1200}


def test_gradual_patch_and_dockerfile():
    text = regression.gradual_dockerfile(BASE)
    assert text == (ROOT / 'docker' / 'Dockerfile.payment-gradual-regression').read_text()
    assert 'sentinelops.dev/variant="payment-gradual-regression"' in text
    for key, value in regression.GRADUAL_DEFAULTS.items():
        assert f'sentinelops.dev/{key.replace("_", "-")}="{value}"' in text
    assert inspect.getsource(regression.gradual_delay_ms).split('\n')[0] in regression.gradual_patch(**regression.GRADUAL_DEFAULTS)
    app = (ROOT / 'services' / 'payment' / 'app.py').read_text()
    for anchor in ('fault = FaultState()', '    await fault.apply(SERVICE_NAME)'):
        assert app.count(anchor) == 1
    namespace = {}
    patched = app
    exec(compile(regression.gradual_patch(**regression.GRADUAL_DEFAULTS).replace(
        "Path('/app/services/payment/app.py')", 'TARGET'), 'patch', 'exec'),
        {'TARGET': type('P', (), {'read_text': lambda self: patched,
                                  'write_text': lambda self, v: namespace.update(out=v)})()})
    assert 'gradual_delay_ms(' in namespace['out'] and namespace['out'].count('await fault.apply(SERVICE_NAME)') == 1
    for bad in ({'start_delay_s': 121}, {'ramp_s': 299}, {'ramp_s': 901}, {'max_ms': 799}, {'max_ms': 2001}):
        with pytest.raises(ValueError):
            regression.gradual_dockerfile(BASE, **{**regression.GRADUAL_DEFAULTS, **bad})
    with pytest.raises(ValueError):
        regression.gradual_dockerfile(BASE.replace('sentinelops/application', 'other/repo'))


def test_console_gradual_actions():
    ids = {action.id for action in console.ACTIONS}
    assert {'payment-gradual-fault', 'deploy-gradual-image', 'reset-demo'} <= ids
    settings = console.Settings()
    runners = console.script_runners(settings)
    for action in ('deploy-gradual-image', 'reset-demo'):
        assert runners[action].configuration_error()
    assert 'DEMO_CONSOLE_GRADUAL_SCRIPT' in runners['deploy-gradual-image'].configuration_error()
    assert 'DEMO_CONSOLE_RESET_SCRIPT' in runners['reset-demo'].configuration_error()
    assert runners['payment-gradual-fault'].configuration_error() is None
    schedule = gradual_fault.schedule(duration_s=1200, step_s=30, max_ms=1200)
    assert schedule[0] == (0, 60) and schedule[1] == (30, 60) and schedule[-1] == (1170, 1170)
    assert len(schedule) == 40 and all(b[1] >= a[1] for a, b in zip(schedule, schedule[1:]))


def test_round_receipt_v2_fields():
    clocks = {'deploy_started_at': '2026-12-01T10:00:00Z', 'change_observed_at': '2026-12-01T10:00:20Z',
              'first_breach_at': '2026-12-01T10:07:00Z', 'incident_created_at': '2026-12-01T10:09:30Z',
              'proposal_created_at': '2026-12-01T10:10:00Z', 'approved_at': '2026-12-01T10:12:00Z',
              'recovered_at': '2026-12-01T10:20:00Z'}
    proposal = {'proposal_version': 'runbook-proposal/v2', 'approval_policy': 'pending-until-decision/v1',
                'proposal_id': 'a' * 64}
    receipt = round_receipt(invocation='run-1', round_number=1, scenario='gradual-regression', clocks=clocks,
        episodes={'payment-service': ['2026-12-01T09:00:00Z', '2026-12-01T10:03:00Z']}, rca_top1='payment-service',
        proposal=proposal, action={'phase': 'recovered', 'telemetry_verified': True}, dispatch_count=1,
        notifications={'early_warning': 1, 'proposal_pending': 1, 'outcome': 1},
        image_params=regression.GRADUAL_DEFAULTS)
    assert receipt['receipt_version'] == 'runbook-demo-round/v2'
    assert receipt['warning'] == {'service': 'payment-service', 'episode_started_at': '2026-12-01T10:03:00Z'}
    assert receipt['lead_seconds'] == 240 and receipt['deploy_to_breach_seconds'] == 420
    assert check_demo_round.problems(receipt) == []
    late = round_receipt(invocation='run-1', round_number=1, scenario='gradual-regression', clocks=clocks,
        episodes={'payment-service': ['2026-12-01T10:08:00Z']}, rca_top1='payment-service', proposal=proposal,
        action={'phase': 'recovered', 'telemetry_verified': True}, dispatch_count=1,
        notifications={'early_warning': 1, 'proposal_pending': 1, 'outcome': 1}, image_params={})
    assert late['warning']['episode_started_at'] is None and late['lead_seconds'] is None
    assert 'warning' in check_demo_round.problems(late)
    json.dumps(receipt)
