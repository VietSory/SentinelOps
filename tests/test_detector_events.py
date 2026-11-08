"""ADR-015 evaluation-only detector events: software boundaries, never G3 evidence."""
from dataclasses import asdict
import json
import subprocess
from types import SimpleNamespace

import pytest

import sentinelops.experiments.local_corpus as corpus
from sentinelops.contracts import utc_time
from sentinelops.experiments.local_recovery import abandon_interrupted_group, read_abandonment
from sentinelops.experiments.clock_recovery import abandon_clock_invalid_group
from sentinelops.experiments.traffic import s1_error_responses
from sentinelops.ml import detector_evaluation as dev
from sentinelops.ml.local_data import (audit_detector_events, audit_local_corpus, detector_evaluation_inventory)
from sentinelops.ml.local_evaluation import load_protocol, summarize
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore


def detector_plan():
    return corpus.load_plan(corpus.DETECTOR_PLAN)


def test_detector_plan_fixes_adr015_protocol_and_rejects_weakened_fields(tmp_path):
    plan = detector_plan()
    assert corpus.plan_kinds(plan) == ('healthy', 'payment-abrupt', 'inventory-abrupt')
    assert corpus.plan_roles(plan) == ('development-validation',)
    for change in ({'fitting_allowed': True}, {'minimum_groups': {'development-validation': 4}},
                   {'kinds': ['healthy', 'payment-gradual', 'inventory-gradual']},
                   {'evaluation_protocol': 'local-model-development/v1'}, {'abrupt_active_seconds': 300},
                   {'sealed_final_capture_allowed': True}, {'roles_in_order': ['train', 'development-validation']},
                   {'extra': 1}):
        path = tmp_path / 'plan.json'
        path.write_text(json.dumps({**plan, **change}))
        with pytest.raises(ValueError):
            corpus.load_plan(path)


def test_detector_recipes_are_abrupt_s1_with_predeclared_mode_parity():
    plan = detector_plan()
    role = 'development-validation'
    odd, even = (corpus.recipe(plan, role, n, 'payment-abrupt') for n in (1, 2))
    assert (odd.scenario, odd.fault_mode, even.fault_mode) == ('S1', 'latency', 'error')
    assert odd.active_seconds == 600 and odd.maximum_delay_ms == 1200 and odd.all_entry_traffic
    assert corpus.recipe(plan, role, 3, 'inventory-abrupt').target_service == 'inventory-service'
    healthy = corpus.recipe(plan, role, 1, 'healthy')
    assert healthy.scenario == 'S0' and healthy.active_seconds == 900
    seeds = {corpus.recipe(plan, role, n, k).seed for n in range(1, 11) for k in corpus.DETECTOR_KINDS}
    assert len(seeds) == 30
    assert corpus.group_name(plan, role, 7) == 'det-07'
    for bad in (lambda: corpus.recipe(plan, role, 1, 'payment-gradual'),
                lambda: corpus.group_name(plan, 'train', 1), lambda: corpus.group_name(plan, 'sealed-final', 1),
                lambda: corpus.group_name(plan, role, 11)):
        with pytest.raises(ValueError):
            bad()


def test_fitting_plan_is_unchanged_and_rejects_abrupt_kinds():
    plan = corpus.load_plan()
    assert corpus.plan_kinds(plan) == corpus.KINDS and corpus.plan_roles(plan) == corpus.ROLES
    assert corpus.recipe(plan, 'train', 1, 'payment-gradual').scenario == 'S3'
    with pytest.raises(ValueError):
        corpus.recipe(plan, 'development-validation', 1, 'payment-abrupt')
    assert corpus.group_name(plan, 'development-validation', 1) == 'dev-01'


def test_detector_and_fitting_epochs_cannot_share_a_store(tmp_path, monkeypatch):
    store, plan, fitting = ArtifactStore(tmp_path), detector_plan(), corpus.load_plan()
    monkeypatch.setattr(corpus, 'registered_sessions', lambda s: [{'group_id': 'train-01', 'role': 'train',
                                                                    'plan_sha256': stable_id(fitting)}])
    with pytest.raises(ValueError, match='share a store'):
        corpus.require_role_order(store, plan, 'development-validation')
    monkeypatch.setattr(corpus, 'registered_sessions', lambda s: [{'group_id': 'det-01', 'role': 'development-validation',
                                                                    'plan_sha256': stable_id(plan)}])
    with pytest.raises(ValueError, match='share a store'):
        corpus.require_role_order(store, fitting, 'train')
    corpus.require_role_order(store, plan, 'development-validation')


def test_frozen_detector_epoch_rejects_new_acquisition(tmp_path):
    (tmp_path / 'evaluation-freeze.json').write_text('{}')
    with pytest.raises(ValueError, match='frozen'):
        corpus.register_session(ArtifactStore(tmp_path), detector_plan(), role='development-validation', ordinal=1)


def test_slot_pattern_admits_only_registered_kind_families():
    for slot in ('train-01-healthy', 'dev-05-inventory-gradual', 'det-01-payment-abrupt', 'det-10-healthy'):
        assert corpus.SLOT_PATTERN.fullmatch(slot)
    for slot in ('det-01-payment-gradual', 'dev-01-payment-abrupt', 'final-01-healthy', 'det-1-healthy'):
        assert not corpus.SLOT_PATTERN.fullmatch(slot)


def outcome(entry, phase, status):
    return {'request_entry_service': entry, 'phase': phase, 'status': status}


def test_s1_error_accepts_503_only_from_the_direct_target_during_fault():
    config = SimpleNamespace(scenario='S1', fault_mode='error', target_service='payment-service')
    good = [outcome('payment-service', 'fault', 503), outcome('checkout-service', 'fault', 502),
            outcome('inventory-service', 'fault', 200), outcome('payment-service', 'warmup', 200)]
    assert s1_error_responses(good, config)
    for bad in (outcome('inventory-service', 'fault', 503), outcome('payment-service', 'warmup', 503),
                outcome('payment-service', 'recovery', 503)):
        assert not s1_error_responses([*good, bad], config)
    for other in (SimpleNamespace(scenario='S1', fault_mode='latency', target_service='payment-service'),
                  SimpleNamespace(scenario='S0', fault_mode='latency', target_service='payment-service'),
                  SimpleNamespace(scenario='S3', fault_mode='latency', target_service='payment-service')):
        assert not s1_error_responses(good, other)


def test_detector_epoch_has_no_replacement_protocol(tmp_path):
    store, plan = ArtifactStore(tmp_path), detector_plan()
    for call in (lambda: abandon_interrupted_group(store, plan, role='development-validation', ordinal=1, replacement_ordinal=6),
                 lambda: abandon_clock_invalid_group(store, plan, role='development-validation', ordinal=1, replacement_ordinal=6)):
        with pytest.raises(ValueError, match='replacement'):
            call()
    session = {'group_id': 'det-01'}
    closed = tmp_path / 'sessions/det-01/closed.json'
    closed.parent.mkdir(parents=True)
    value = {'closure_version': 'local-http-host-interruption/v1'}
    corpus.bind(store, closed, value)
    with pytest.raises(ValueError, match='replacement'):
        read_abandonment(store, session, plan)


def test_audits_refuse_the_other_plan_and_unready_inventories(tmp_path):
    store = ArtifactStore(tmp_path)
    with pytest.raises(ValueError, match='evaluation-only'):
        audit_local_corpus(store, plan=detector_plan())
    with pytest.raises(ValueError, match='ADR-015'):
        audit_detector_events(store, corpus.load_plan())
    for report in ({'dataset_version': 'local-http-admission/v1', 'evaluation_ready': True, 'created_at': 'x'},
                   {'dataset_version': 'local-http-detector-events-admission/v1', 'evaluation_ready': False}):
        with pytest.raises(DatasetNotReady):
            detector_evaluation_inventory(store, detector_plan(), report)


def at(minute, second=0):
    return f"2026-11-01T00:{minute:02d}:{second:02d}Z"


def run_case(run, group, onset, *, breach_from=None, minutes=14, service='payment-service'):
    base = int(utc_time(at(0)).timestamp())
    breach_from = breach_from if breach_from is not None else (onset if onset is not None else minutes)
    return {'run_id': run, 'group_id': group, 'role': 'development-validation', 'ended_at': at(minutes),
            'gradual_control_verified': True,
            'oracle': {'services': {service: {'first_violation_at': at(onset) if onset is not None else None,
                'buckets': {str(base + i * 60): {'availability': 'VERIFIED', 'breach': i >= breach_from,
                                                'p95_latency_ms': 100. + i} for i in range(minutes)}}}}}


def rows_for(run, group, flags, positives=(), first=1):
    return [{'run_id': run, 'group_id': group, 'window_end': at(first + i), 'decision_at': at(first + i, 30),
             'risk_label': 1 if first + i in positives else 0, 'isolation_forest_flag': f, 'median_mad_flag': False,
             'xgboost_flag': False, 'slope_flag': False, 'xgboost_score': .1, 'slope_score': .1}
            for i, f in enumerate(flags)]


def test_detector_summary_without_separation_matches_v1_detector_counts():
    pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    protocol = load_protocol()
    cases = [run_case('run-a', 'g1', 8), run_case('run-b', 'g2', None)]
    rows = (rows_for('run-a', 'g1', [False, False, True, True, True, True, True, True, False, False, False, False])
            + rows_for('run-b', 'g2', [False, True, True, False, False, False, False, False, False, False, False, False]))
    v1 = summarize(rows, cases, 'payment-service', protocol)['counterfactual_detector_events']
    v2 = dev.detector_summary(rows, cases, 'payment-service', protocol)
    for name in dev.DETECTORS:
        assert v2[name] == v1[name]


def test_precursor_separation_never_credits_detection_and_removes_healthy_time():
    protocol = load_protocol()
    case = run_case('run-a', 'g1', 9)
    # Episode enters at minute 5 inside the verified positive windows 5..8; onset 9.
    rows = rows_for('run-a', 'g1', [False, False, False, True, True, True, True, True, True, True],
                    positives=range(5, 9))
    plain = dev.detector_summary(rows, [case], 'payment-service', protocol)['isolation_forest']
    separated = dev.detector_summary(rows, [case], 'payment-service', protocol, separate_precursor=True)['isolation_forest']
    assert (plain['tp'], plain['fp'], plain['fn']) == (0, 1, 1)
    assert (separated['tp'], separated['fp'], separated['fn']) == (0, 0, 1)
    assert separated['precursor_interval_episodes'] == 1
    assert separated['eligible_healthy_service_hours'] < plain['eligible_healthy_service_hours']
    # An episode before the verified interval keeps v1 accounting.
    early = rows_for('run-a', 'g1', [False, True, True, True, True, True, True, True, True, True], positives=range(6, 9))
    kept = dev.detector_summary(early, [case], 'payment-service', protocol, separate_precursor=True)['isolation_forest']
    assert (kept['fp'], kept['precursor_interval_episodes']) == (1, 0)


def test_abrupt_detection_inside_deadline_is_a_true_positive_and_late_is_not():
    protocol = load_protocol()
    case = run_case('run-a', 'g1', 6)
    timely = rows_for('run-a', 'g1', [False, False, False, False, False, True, True, True, True])
    late = rows_for('run-a', 'g1', [False] * 10 + [True, True, True], first=1)
    hit = dev.detector_summary(timely, [case], 'payment-service', protocol)['isolation_forest']
    miss = dev.detector_summary(late, [case], 'payment-service', protocol)['isolation_forest']
    assert (hit['tp'], hit['fn']) == (1, 0) and hit['detection_delays_seconds'][0] <= 300
    assert (miss['tp'], miss['fp'], miss['fn']) == (0, 1, 1)


def test_detector_bootstrap_needs_five_whole_groups_and_is_reproducible():
    pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    protocol = load_protocol()
    cases, rows = [], []
    for g in range(5):
        cases.append(run_case(f'run-{g}', f'g{g}', 6))
        rows.extend(rows_for(f'run-{g}', f'g{g}', [False] * 5 + [True] * 4))
    first = dev.paired_detector_interval(rows, cases, 'payment-service', protocol)
    assert first == dev.paired_detector_interval(rows, cases, 'payment-service', protocol)
    assert first['groups'] == 5 and first['if_minus_mad_event_f1']['lower'] == 1.0
    with pytest.raises(DatasetNotReady):
        dev.paired_detector_interval(rows, cases[:4], 'payment-service', protocol)


def test_protocol_v2_is_bound_to_unchanged_v1_and_cannot_open_final(tmp_path):
    value, base = dev.load_protocol_v2()
    assert value['base_protocol_sha256'] == stable_id(base) and not value['final_access_allowed']
    (tmp_path / 'configs').mkdir()
    (tmp_path / 'configs/local_model_evaluation.json').write_text(json.dumps(base))
    for change in ({'final_access_allowed': True}, {'base_protocol_sha256': '0' * 64}, {'extra': 1}):
        (tmp_path / 'configs/local_model_evaluation_v2.json').write_text(json.dumps({**value, **change}))
        with pytest.raises(ValueError):
            dev.load_protocol_v2(tmp_path)


def test_protocol_must_be_committed_before_every_registered_capture(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    monkeypatch.setattr(corpus, 'registered_sessions', lambda s: [])
    with pytest.raises(DatasetNotReady, match='no registered'):
        dev.require_frozen_before_capture(store)
    monkeypatch.setattr(corpus, 'registered_sessions', lambda s: [{'source_commit': '0' * 40}])

    def missing(*args, **kwargs):
        raise subprocess.CalledProcessError(128, args[0])
    monkeypatch.setattr(dev.subprocess, 'check_output', missing)
    with pytest.raises(DatasetNotReady, match='before capture'):
        dev.require_frozen_before_capture(store)
    monkeypatch.setattr(dev.subprocess, 'check_output', lambda *a, **k: b'{"changed": true}')
    with pytest.raises(DatasetNotReady, match='changed after'):
        dev.require_frozen_before_capture(store)


def test_campaign_plans_exactly_the_minimum_detector_groups_and_keeps_v1_slots():
    from sentinelops.experiments.local_campaign import planned_slots
    slots = planned_slots(detector_plan())
    assert len(slots) == 15 and {k for _, _, k in slots} == set(corpus.DETECTOR_KINDS)
    assert {r for r, _, _ in slots} == {'development-validation'}
    v1 = planned_slots(corpus.load_plan())
    assert len(v1) == 30 and {k for _, _, k in v1} == set(corpus.KINDS)
