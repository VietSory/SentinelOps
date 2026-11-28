from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
pytest.importorskip("sklearn")

from sentinelops.ml.model_benchmark_forecast import MODELS
from sentinelops.ml.model_benchmark_alibaba_events import ALIBABA_EVENT_RULE_ID
from sentinelops.ml.model_benchmark_detector_combo import COMBO_SELECTION_RULE_ID
from sentinelops.ml.model_benchmark_protocol import LANE_B_BASELINE, LANE_B_CANDIDATE, LANE_B_COST_METRIC, lane_b_rules
from sentinelops.ml.model_benchmark_reports import forecast_report
from sentinelops.ml.model_benchmark_reports import lane_b_recommendation
from sentinelops.ml.model_benchmark_reports import summarize_sealed_forecasts

PROVENANCE = dict(code='fixture', config='fixture', data='fixture', models='fixture')
THRESHOLDS = {model: dict(watch=.4, degrading=.7) for model in MODELS}
LANE_B_RULE_IDS = {'event_rule_id': ALIBABA_EVENT_RULE_ID, 'combo_selection_rule_id': COMBO_SELECTION_RULE_ID}


def fixture(leads=(59, 60, 180, 200, 240)):
    rows, events = [], []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index, lead in enumerate(leads):
        run = str(index)
        for minute, score in ((5, .5), (6, .8)):
            for model in MODELS:
                rows.append(dict(run_id=run, service='s', group_id=run, model=model,
                    window_end=(start + timedelta(minutes=minute)).isoformat(),
                    prediction_cutoff=(start + timedelta(minutes=minute, seconds=30)).isoformat(),
                    score=score, risk_label=1, feature_ref='f', oracle_sha256='o',
                    health_policy_sha256='h', risk_missing_reason=None, slo_onset_at=None))
        events.append(dict(event_id=run, run_id=run, service='s', group_id=run,
                           onset_at=(start + timedelta(minutes=6, seconds=30 + lead)).isoformat()))
    return rows, events


def report(rows, events, **kwargs):
    return forecast_report(rows, events, thresholds=THRESHOLDS, policy_identity='fixture-only',
                           provenance=PROVENANCE, **kwargs)


def test_lead_denominators_and_group_bootstrap():
    rows, events = fixture()
    for row in rows:
        if row['model'] == 'ltfp_self_only': row['score'] = None
    before = deepcopy((rows, events))
    result = report(rows, events)
    lead = result['models']['ltfp_graph']['lead']
    assert (lead['ge60'], lead['ge180'], lead['late'], lead['miss'], lead['p50']) == (4, 3, 1, 0, 180)
    assert result['models']['ltfp_self_only']['lead']['events'] == 5
    assert result['models']['ltfp_self_only']['lead']['miss'] == 5
    assert result['bootstrap']['intervals']['ltfp_graph_minus_ltfp_self_only']['valid_replicates'] == 1000
    assert result['bootstrap']['intervals']['ltfp_graph_minus_ltfp_self_only']['upper'] == 1
    assert result['bootstrap']['intervals']['ltfp_graph_minus_xgboost']['lower'] == 0
    paired = result['paired_lead']['ltfp_graph_minus_xgboost']
    assert paired['common_matched_events'] == 5 and paired['delta_p50_seconds'] == 0
    assert result['paired_lead']['ltfp_graph_minus_ltfp_self_only']['right_misses'] == 5
    assert report(list(reversed(rows)), list(reversed(events))) == result
    assert (rows, events) == before
    assert result['models']['slope']['no_breach']['warnings'] is None
    assert result['models']['slope']['healthy_exposure']['per_service_hour'] is None


def test_independent_truth_unknown_and_exposure():
    rows, _ = fixture((60,))
    for row in rows: row['risk_label'] = None
    truth = [dict(run_id='0', service='s', group_id='0', outcome='no-breach',
                  healthy_hours=2 / 60, evidence_id='verified-fixture',
                  score_schedule=dict(start='2026-01-01T00:05:00Z', stop='2026-01-01T00:07:00Z',
                                      cadence_seconds=60, cutoff_delay_seconds=30))]
    result = report(rows, [], truth=truth)
    assert result['summary']['unknown_windows'] == 2
    assert result['models']['slope']['healthy_exposure']['per_service_hour'] == 30
    assert result['models']['slope']['no_breach']['warned_runs'] == 1
    for mutation in ({'healthy_hours': -1}, {'healthy_hours': float('nan')},
                     {'outcome': 'UNKNOWN'}, {'group_id': 'wrong'}):
        with pytest.raises(ValueError): report(rows, [], truth=[{**truth[0], **mutation}])
    with pytest.raises(ValueError): report(rows, [], truth=truth * 2)
    with pytest.raises(ValueError): report(rows, fixture((60,))[1], truth=truth)


def test_sealed_summary_separates_gradual_and_healthy_denominators():
    rows, events = fixture((60,))
    schedule = dict(start='2026-01-01T00:05:00Z', stop='2026-01-01T00:07:00Z',
                    cadence_seconds=60, cutoff_delay_seconds=30)
    breach = dict(run_id='0', service='s', group_id='0', outcome='breach', evidence_id='oracle',
                  score_schedule=schedule)
    def nonbreach(run, group, kind):
        copied = [{**row, 'run_id': run, 'group_id': group, 'risk_label': 0} for row in rows]
        truth = dict(run_id=run, service='s', group_id=group, outcome='no-breach', evidence_id='oracle',
                     healthy_hours=2 / 60, score_schedule=schedule)
        return dict(run_id=run, group_id=group, kind=kind, policy_sha256='policy', rows=copied, events=[], truth=[truth])
    cases = [dict(run_id='0', group_id='0', kind='payment-gradual-high', policy_sha256='policy',
                  rows=rows, events=events, truth=[breach]),
             nonbreach('low', 'g1', 'payment-gradual-low'), nonbreach('healthy', 'g2', 'healthy')]
    scored = dict(scope='sealed-local-latency-holdout', clock='recorded-source-availability-replay',
                  scorer_commit='code', capture_commit='capture', candidates_sha256='models', seal_sha256='seal',
                  activation_allowed=False, cases=cases,
                  thresholds={'thresholds': THRESHOLDS, 'policy_identity': 'frozen'})
    result = summarize_sealed_forecasts(scored)
    gradual = result['reports']['gradual']['models']['slope']
    healthy = result['reports']['healthy']['models']['slope']
    assert gradual['lead']['events'] == 1 and gradual['no_breach']['runs'] == 1
    assert gradual['healthy_exposure']['runs'] == 0 and healthy['healthy_exposure']['runs'] == 1
    assert healthy['healthy_exposure']['service_hours'] == 2 / 60
    with pytest.raises(ValueError, match='duplicate or invalid'):
        summarize_sealed_forecasts({**scored, 'cases': cases + cases[:1]})


def test_no_breach_counts_runs_once_and_excludes_breached_or_unknown_runs():
    from sentinelops.telemetry.core import BUSINESS_ROUTES

    base, events = fixture((60,))
    schedule = dict(start='2026-01-01T00:05:00Z', stop='2026-01-01T00:07:00Z',
                    cadence_seconds=60, cutoff_delay_seconds=30)
    services = sorted(BUSINESS_ROUTES)
    def case(run, outcomes):
        rows = [{**row, 'run_id': run, 'group_id': run, 'service': service, 'risk_label': 0}
                for service in services for row in base]
        truth = [dict(run_id=run, service=service, group_id=run, outcome=outcome, evidence_id='oracle',
                      score_schedule=schedule) for service, outcome in zip(services, outcomes)]
        actual_events = [{**events[0], 'event_id': run, 'run_id': run, 'group_id': run, 'service': services[0]}]
        return dict(run_id=run, group_id=run, kind='payment-gradual-low', policy_sha256='policy', rows=rows,
                    events=actual_events if 'breach' in outcomes else [], truth=truth)
    cases = [case('no-breach', ['no-breach'] * 3),
             case('breached', ['breach', 'no-breach', 'no-breach']),
             case('unknown', ['no-breach', 'UNKNOWN', 'no-breach'])]
    scored = dict(scope='sealed-local-latency-holdout', clock='recorded-source-availability-replay',
                  scorer_commit='code', capture_commit='capture', candidates_sha256='models', seal_sha256='seal',
                  activation_allowed=False, cases=cases, thresholds={'thresholds': THRESHOLDS, 'policy_identity': 'frozen'})
    result = summarize_sealed_forecasts(scored)['reports']['gradual']['run_no_breach']['slope']
    assert result == {'runs': 1, 'warned_runs': 1, 'unknown_runs': 1, 'missing_reason': None}
    cases[0]['rows'][0]['score'] = None
    result = summarize_sealed_forecasts(scored)['reports']['gradual']['run_no_breach']['slope']
    assert result['runs'] == 1 and result['warned_runs'] is None
    assert result['missing_reason'] == 'incomplete-scored-exposure'


def test_lane_a_recommendation_requires_paired_gain_and_no_higher_warning_cost():
    from sentinelops.ml.model_benchmark_reports import lane_a_recommendation

    sample = {'runs': 5, 'warned_runs': 1, 'unknown_runs': 0, 'missing_reason': None}
    gradual = {'bootstrap': {'intervals': {'ltfp_graph_minus_xgboost': {'lower': .1}}},
               'primary_event_coverage': {'events': 1, 'incomplete_events': 0},
               'run_no_breach': {'ltfp_graph': dict(sample), 'xgboost': dict(sample)}}
    exposure = {'coverage_complete': True, 'per_service_hour': .2}
    healthy = {'models': {'ltfp_graph': {'healthy_exposure': dict(exposure)},
                          'xgboost': {'healthy_exposure': dict(exposure)}}}
    rules = {'calibration_budget': .01, 'calibration_cohort': 'shared'}
    decide = lambda q07={'status': 'PASS'}: lane_a_recommendation(gradual, healthy, q07, rules)
    assert decide()['benchmark'] == 'REPLACE' and decide()['release'] == 'KEEP'
    assert decide({'status': 'FAIL'})['benchmark'] == 'KEEP'
    assert decide({'status': 'INCONCLUSIVE'})['benchmark'] == 'INCONCLUSIVE'
    interval = gradual['bootstrap']['intervals']['ltfp_graph_minus_xgboost']
    interval['lower'] = -.051
    assert decide()['reason'] == 'recall-non-inferiority-not-established'
    interval['lower'] = -.05
    assert decide()['benchmark'] == 'INCONCLUSIVE'
    assert decide()['reason'] == 'non-inferior-without-measured-advantage'
    gradual['run_no_breach']['ltfp_graph']['warned_runs'] = 0
    assert decide()['reason'] == 'recall-non-inferior-with-lower-warning-cost'
    gradual['run_no_breach']['ltfp_graph']['warned_runs'] = 1
    healthy['models']['ltfp_graph']['healthy_exposure']['per_service_hour'] = .1
    assert decide()['benchmark'] == 'REPLACE'
    healthy['models']['ltfp_graph']['healthy_exposure']['per_service_hour'] = .2
    interval['lower'] = 0
    assert decide()['reason'] == 'non-inferior-without-measured-advantage'
    interval['lower'] = .1
    gradual['run_no_breach']['ltfp_graph']['unknown_runs'] = 1
    assert decide()['reason'] == 'false-warning-comparison-incomplete'
    gradual['run_no_breach']['ltfp_graph']['unknown_runs'] = 0
    healthy['models']['ltfp_graph']['healthy_exposure']['per_service_hour'] = .3
    assert decide()['benchmark'] == 'KEEP' and decide()['reason'] == 'higher-false-warning-cost'
    healthy['models']['ltfp_graph']['healthy_exposure']['per_service_hour'] = .2
    rules['calibration_budget'] = None
    assert decide()['reason'] == 'common-calibration-budget-unverified'


def lane_b_evaluation(lower, cost_upper, candidate_cost):
    def interval(lower, upper):
        return dict(lower=lower, upper=upper, valid_replicates=1000, undefined_replicates=0,
                    denominators={LANE_B_CANDIDATE: 12, LANE_B_BASELINE: 12}, missing_reason=None)

    model = dict(healthy_scored_service_hours=10., healthy_false_incidents=3)
    return {**LANE_B_RULE_IDS, 'event_kind': 'alibaba-proxy',
            'coverage': {'pairs': {'adsketch_mad_or_minus_isolation_forest': {
                'healthy_minutes': 20, 'healthy_both_scored': 20,
                'events': 20, 'events_both_scored': 20, 'coverage': 1.}}},
            'models': {LANE_B_CANDIDATE: {**model, LANE_B_COST_METRIC: candidate_cost},
                       LANE_B_BASELINE: {**model, LANE_B_COST_METRIC: .4}},
            'bootstrap': {'event_groups': 5, 'groups': 100, 'intervals': {
                'adsketch_mad_or_minus_isolation_forest': {'f1': interval(lower, .2),
                                                          LANE_B_COST_METRIC: interval(-.2, cost_upper)},
                'median_mad_minus_isolation_forest': {'f1': interval(-.2, .1),
                                                     LANE_B_COST_METRIC: interval(-.1, .3)},
                'adsketch_mad_or_minus_median_mad': {'f1': interval(-.02, .2),
                                                   LANE_B_COST_METRIC: interval(-.1, .3)},
                'adsketch_minus_isolation_forest': {'f1': interval(-.3, .4),
                                                   LANE_B_COST_METRIC: interval(-.2, .4)},
                'adsketch_minus_median_mad': {'f1': interval(-.4, .3),
                                            LANE_B_COST_METRIC: interval(-.2, .3)}}}}


@pytest.mark.parametrize('lower, cost_upper, candidate_cost, benchmark, reason', [
    (.01, 0., .4, 'REPLACE', 'f1-improved-without-higher-cost'),
    (-.05, -.01, .3, 'REPLACE', 'non-inferior-with-lower-false-incident-cost'),
    (-.05, 0., .3, 'INCONCLUSIVE', 'non-inferior-without-measured-advantage'),
    (0., 0., .4, 'INCONCLUSIVE', 'non-inferior-without-measured-advantage'),
    (-.05001, -.01, .3, 'INCONCLUSIVE', 'f1-non-inferiority-not-established'),
    (.01, .2, .5, 'KEEP', 'higher-false-incident-cost'),
])
def test_lane_b_recommendation_decision_table(lower, cost_upper, candidate_cost, benchmark, reason):
    evaluation = lane_b_evaluation(lower, cost_upper, candidate_cost)
    result = lane_b_recommendation(evaluation, lane_b_rules(**LANE_B_RULE_IDS))
    assert (result['benchmark'], result['reason']) == (benchmark, reason)
    assert result['release'] == 'KEEP' and result['activation_allowed'] is False and result['runtime_parity'] == 'NOT_RUN'
    assert result['novel_pattern'] == {'status': 'NA', 'reason': 'insufficient-support'}
    assert result['local_gates'] == dict.fromkeys(('Q-04', 'Q-05', 'Q-06'), 'NOT_RUN')
    assert result['scope'] == 'alibaba-proxy-ranking' and result['uses_proxy_positive_labels'] is True
    mad = result['comparisons']['median_mad_minus_isolation_forest']
    assert (mad['f1']['lower'], mad['f1']['upper']) == (-.2, .1)
    assert (mad[LANE_B_COST_METRIC]['lower'], mad[LANE_B_COST_METRIC]['upper']) == (-.1, .3)
    assert result['comparisons'] == evaluation['bootstrap']['intervals']
    assert list(result['comparisons']) == [
        'adsketch_mad_or_minus_isolation_forest', 'median_mad_minus_isolation_forest',
        'adsketch_mad_or_minus_median_mad', 'adsketch_minus_isolation_forest', 'adsketch_minus_median_mad']


@pytest.mark.parametrize('case, reason', [
    ('healthy-only-groups', 'insufficient-event-support'),
    ('support-missing', 'insufficient-event-support'),
    ('coverage-incomplete', 'pairwise-coverage-incomplete'),
    ('coverage-missing', 'pairwise-coverage-incomplete'),
    ('f1-bootstrap-missing', 'bootstrap-undefined'),
    ('cost-bootstrap-missing', 'bootstrap-undefined'),
    ('rate-missing', 'bootstrap-undefined'),
    ('preregistration-missing', 'rules-unverified'),
])
def test_lane_b_recommendation_rejects_incomplete_evidence(case, reason):
    evaluation = lane_b_evaluation(.01, -.01, .3)
    rules = lane_b_rules(**LANE_B_RULE_IDS)
    if case == 'healthy-only-groups':
        evaluation['bootstrap']['event_groups'] = 4
    elif case == 'support-missing':
        del evaluation['bootstrap']['event_groups']
    elif case == 'coverage-incomplete':
        evaluation['coverage']['pairs']['adsketch_mad_or_minus_isolation_forest'].update(
            events_both_scored=18, coverage=.9)
    elif case == 'coverage-missing':
        del evaluation['coverage']['pairs']['adsketch_mad_or_minus_isolation_forest']
    elif case == 'f1-bootstrap-missing':
        evaluation['bootstrap']['intervals']['adsketch_mad_or_minus_isolation_forest']['f1'].update(
            missing_reason='no-defined-paired-replicates', valid_replicates=0, lower=None, upper=None)
    elif case == 'cost-bootstrap-missing':
        evaluation['bootstrap']['intervals']['adsketch_mad_or_minus_isolation_forest'][LANE_B_COST_METRIC].update(
            missing_reason='insufficient-valid-replicates')
    elif case == 'rate-missing':
        evaluation['models'][LANE_B_CANDIDATE][LANE_B_COST_METRIC] = None
    elif case == 'preregistration-missing':
        rules = {}
    result = lane_b_recommendation(evaluation, rules)
    assert (result['benchmark'], result['reason']) == ('INCONCLUSIVE', reason)
    assert result['release'] == 'KEEP' and result['activation_allowed'] is False and result['runtime_parity'] == 'NOT_RUN'


@pytest.mark.parametrize('field, value', [
    ('calibration_budget', None), ('calibration_budget', .02), ('calibration_budget', '.01'),
    ('calibration_cohort', ''), ('calibration_cohort', ' '), ('calibration_cohort', []),
    ('event_rule_id', None), ('combo_selection_rule_id', ''),
])
def test_lane_b_recommendation_requires_calibration_and_rule_identity(field, value):
    rules = lane_b_rules(**LANE_B_RULE_IDS)
    rules[field] = value
    result = lane_b_recommendation(lane_b_evaluation(.01, -.01, .3), rules)
    assert result['benchmark'] == 'INCONCLUSIVE' and result['reason'] == 'rules-unverified'


@pytest.mark.parametrize('field', ['event_rule_id', 'combo_selection_rule_id'])
@pytest.mark.parametrize('invalid', ['missing', '0' * 64, None])
def test_lane_b_recommendation_requires_evaluation_rule_attestation(field, invalid):
    evaluation = lane_b_evaluation(.01, -.01, .3)
    rules = lane_b_rules(**LANE_B_RULE_IDS)
    assert lane_b_recommendation(evaluation, rules)['benchmark'] == 'REPLACE'
    if invalid == 'missing':
        del evaluation[field]
    else:
        evaluation[field] = invalid
    evaluation['bootstrap']['event_groups'] = 4
    result = lane_b_recommendation(evaluation, rules)
    assert (result['benchmark'], result['reason']) == ('INCONCLUSIVE', 'rules-unverified')
    assert result['activation_allowed'] is False and result['release'] == 'KEEP'


@pytest.mark.parametrize('field, reason', [
    ('event_rule_id', 'event-rule-id-mismatch'), ('combo_selection_rule_id', 'combo-rule-id-mismatch'),
])
def test_wrong_rule_sha_blocks_freeze_and_gate_on_the_same_ledger(tmp_path, field, reason):
    from sentinelops.ml.model_benchmark_protocol import freeze_protocol
    from test_model_benchmark_protocol import fixture

    protocol, _ = fixture(tmp_path)
    rules = protocol['reporting']['lane_b']
    evaluation = lane_b_evaluation(.01, -.01, .3)
    assert lane_b_recommendation(evaluation, rules)['benchmark'] == 'REPLACE'
    rules[field] = '0' * 64
    frozen = freeze_protocol(protocol, tmp_path, lanes=('b',))
    assert frozen['status'] == 'blocked' and frozen['reasons'] == ['b: ' + reason]
    assert not (tmp_path / 'freeze/protocol.json').exists()
    result = lane_b_recommendation(evaluation, rules)
    assert (result['benchmark'], result['reason']) == ('INCONCLUSIVE', 'rules-unverified')
    evaluation[field] = rules[field]
    assert lane_b_recommendation(evaluation, rules)['reason'] == 'rules-unverified'


@pytest.mark.parametrize('coverage, accepted', [(None, False), (.949, False), (.95, True), (1., True)])
def test_lane_b_recommendation_pairwise_coverage_boundary(coverage, accepted):
    evaluation = lane_b_evaluation(.01, -.01, .3)
    evaluation['coverage']['pairs']['adsketch_mad_or_minus_isolation_forest']['coverage'] = coverage
    evaluation['models'][LANE_B_CANDIDATE]['healthy_scored_service_hours'] = 9.5
    result = lane_b_recommendation(evaluation, lane_b_rules(**LANE_B_RULE_IDS))
    assert result['benchmark'] == ('REPLACE' if accepted else 'INCONCLUSIVE')
    if not accepted:
        assert result['reason'] == 'pairwise-coverage-incomplete'


@pytest.mark.parametrize('metric', ['f1', LANE_B_COST_METRIC])
@pytest.mark.parametrize('replicates, accepted', [(949, False), (950, True)])
def test_lane_b_recommendation_paired_replicate_boundary(metric, replicates, accepted):
    evaluation = lane_b_evaluation(.01, -.01, .3)
    evaluation['bootstrap']['intervals']['adsketch_mad_or_minus_isolation_forest'][metric].update(
        valid_replicates=replicates, undefined_replicates=1000-replicates)
    result = lane_b_recommendation(evaluation, lane_b_rules(**LANE_B_RULE_IDS))
    assert result['benchmark'] == ('REPLACE' if accepted else 'INCONCLUSIVE')
    if not accepted:
        assert result['reason'] == 'bootstrap-undefined'


def test_lane_b_recommendation_branch_priority_and_minimal_rules():
    evaluation = lane_b_evaluation(-.06, -.01, .5)
    rules = {**LANE_B_RULE_IDS, 'calibration_budget': .02, 'calibration_cohort': 'calibration-anchor-fixture'}
    evaluation['bootstrap']['event_groups'] = 4
    coverage = evaluation['coverage']['pairs']['adsketch_mad_or_minus_isolation_forest']
    coverage['coverage'] = .9
    interval = evaluation['bootstrap']['intervals']['adsketch_mad_or_minus_isolation_forest']['f1']
    interval['valid_replicates'] = 949
    assert lane_b_recommendation(evaluation, rules)['reason'] == 'rules-unverified'
    rules['calibration_budget'] = .01
    assert lane_b_recommendation(evaluation, rules)['reason'] == 'insufficient-event-support'
    evaluation['bootstrap']['event_groups'] = 5
    assert lane_b_recommendation(evaluation, rules)['reason'] == 'pairwise-coverage-incomplete'
    coverage['coverage'] = .95
    assert lane_b_recommendation(evaluation, rules)['reason'] == 'bootstrap-undefined'
    interval['valid_replicates'] = 950
    assert lane_b_recommendation(evaluation, rules)['reason'] == 'f1-non-inferiority-not-established'
    interval['lower'] = .01
    assert lane_b_recommendation(evaluation, rules)['reason'] == 'higher-false-incident-cost'
    evaluation['models'][LANE_B_CANDIDATE][LANE_B_COST_METRIC] = .3
    assert lane_b_recommendation(evaluation, rules)['reason'] == 'f1-improved-without-higher-cost'
    interval['lower'] = -.05
    assert lane_b_recommendation(evaluation, rules)['reason'] == 'non-inferior-with-lower-false-incident-cost'
    evaluation['bootstrap']['intervals']['adsketch_mad_or_minus_isolation_forest'][LANE_B_COST_METRIC]['upper'] = 0.
    assert lane_b_recommendation(evaluation, rules)['reason'] == 'non-inferior-without-measured-advantage'


def test_lane_b_recommendation_requires_directed_bootstrap_pairs():
    evaluation = lane_b_evaluation(.01, -.01, .3)
    intervals = evaluation['bootstrap']['intervals']
    intervals['isolation_forest_minus_adsketch_mad_or'] = intervals.pop('adsketch_mad_or_minus_isolation_forest')
    result = lane_b_recommendation(evaluation, lane_b_rules(**LANE_B_RULE_IDS))
    assert result['reason'] == 'bootstrap-undefined'
    assert result['comparisons']['adsketch_mad_or_minus_isolation_forest'] == {}
    assert 'isolation_forest_minus_adsketch_mad_or' not in result['comparisons']


def test_lane_b_recommendation_consumes_evaluator_coverage_and_directed_intervals():
    from sentinelops.ml.model_benchmark_detector_evaluation import MODELS, evaluate_detectors

    windows, events, predictions = [], [], []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    onset = (start + timedelta(minutes=324)).isoformat()
    for index in range(5):
        identity = dict(dataset='alibaba-proxy-fixture', group_id=f'service-{index}',
                        run_id=f'service-{index}', service=f'service-{index}')
        events.append({**identity, 'event_id': f'event-{index}', 'kind': 'alibaba-proxy',
                       'eligible': True, 'onset_at': onset, 'confirmed_at': onset})
        for minute in range(319, 336):
            key = {**identity, 'bucket_end': (start + timedelta(minutes=minute)).isoformat()}
            windows.append({**key, 'eligible': minute >= 324, 'y': int(minute == 324),
                            'healthy': minute >= 330, 'reason': 'warmup' if minute < 324 else None})
            if minute < 324:
                continue
            for model in MODELS:
                predictions.append({**key, 'model_id': model,
                                    'score': .8 if model == 'median_mad' and minute == 324 else .1})

    def evaluate(rows):
        result = evaluate_detectors(windows, events, rows, clock='replay',
            thresholds={**dict.fromkeys(MODELS, .5), LANE_B_CANDIDATE: 1.},
            component_thresholds={'median_mad': .5, 'adsketch': .5}, policy_identity='fixture-policy',
            persistence={'step_seconds': 60, 'entry_windows': 1, 'exit_windows': 1}, event_kind='alibaba-proxy')
        return {**result, **LANE_B_RULE_IDS}

    evaluation = evaluate(predictions)
    rules = lane_b_rules(**LANE_B_RULE_IDS)
    result = lane_b_recommendation(evaluation, rules)
    assert (result['benchmark'], result['reason']) == ('REPLACE', 'f1-improved-without-higher-cost')
    assert result['comparisons'] == evaluation['bootstrap']['intervals']
    primary = result['comparisons']['adsketch_mad_or_minus_isolation_forest']
    assert primary['f1']['lower'] == primary['f1']['upper'] == 1.
    assert primary[LANE_B_COST_METRIC]['valid_replicates'] == 1000
    assert primary[LANE_B_COST_METRIC]['denominators'] == {LANE_B_CANDIDATE: .5, LANE_B_BASELINE: .5}
    missing_at = (start + timedelta(minutes=330)).isoformat()
    incomplete = evaluate([row for row in predictions if not (
        row['model_id'] == LANE_B_BASELINE and row['service'] in ('service-0', 'service-1')
        and row['bucket_end'] == missing_at)])
    assert lane_b_recommendation(incomplete, rules)['reason'] == 'pairwise-coverage-incomplete'


def test_primary_event_coverage_detects_missing_comparator_scores():
    from sentinelops.ml.model_benchmark_reports import primary_event_coverage

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    schedule = {'start': start.isoformat(), 'stop': (start + timedelta(minutes=8)).isoformat(),
                'cadence_seconds': 60, 'cutoff_delay_seconds': 30}
    rows = []
    for minute in range(8):
        for model in ('ltfp_graph', 'xgboost'):
            rows.append({'service': 'payment-service', 'model': model,
                         'window_end': (start + timedelta(minutes=minute)).isoformat(),
                         'prediction_cutoff': (start + timedelta(minutes=minute, seconds=30)).isoformat(), 'score': .5})
    case = {'truth': [{'service': 'payment-service', 'score_schedule': schedule}], 'rows': rows,
            'events': [{'event_id': 'event', 'service': 'payment-service',
                        'onset_at': (start + timedelta(minutes=7)).isoformat()}]}
    result = primary_event_coverage([case])
    assert result == {'events': 1, 'complete_events': 1, 'incomplete_events': 0, 'incomplete_event_ids': []}
    next(row for row in rows if row['model'] == 'xgboost' and row['window_end'] == (start + timedelta(minutes=4)).isoformat())['score'] = None
    assert primary_event_coverage([case])['incomplete_event_ids'] == ['event']


def test_exposure_uses_each_recorded_source_cutoff():
    rows, _ = fixture((60,))
    cutoffs = {'2026-01-01T00:05:00Z': '2026-01-01T00:05:30.125Z',
               '2026-01-01T00:06:00Z': '2026-01-01T00:06:31.875Z'}
    for row in rows:
        anchor = datetime.fromisoformat(row['window_end']).strftime('%Y-%m-%dT%H:%M:%SZ')
        row.update(risk_label=None, prediction_cutoff=cutoffs[anchor])
    truth = [dict(run_id='0', service='s', group_id='0', outcome='no-breach',
                  healthy_hours=2 / 60, evidence_id='verified-fixture',
                  score_schedule=dict(start='2026-01-01T00:05:00Z', stop='2026-01-01T00:07:00Z',
                                      cadence_seconds=60, cutoffs=cutoffs))]
    result = report(rows, [], truth=truth)['models']['slope']['healthy_exposure']
    assert result['coverage_complete'] is True and result['scored_service_hours'] == 2 / 60
    cutoffs['2026-01-01T00:06:00Z'] = None
    result = report(rows, [], truth=truth)['models']['slope']['healthy_exposure']
    assert result['coverage_complete'] is False and result['scored_service_hours'] == 1 / 60
    cutoffs['2026-01-01T00:06:00Z'] = '2026-01-01T00:07:00Z'
    with pytest.raises(ValueError, match='invalid recorded source cutoff'):
        report(rows, [], truth=truth)


@pytest.mark.parametrize('label', [0, 1, None])
def test_class_na_and_unsupported_ci(label):
    rows, events = fixture((60,))
    for row in rows: row['risk_label'] = label
    result = report(rows, events)
    assert result['models']['slope']['average_precision'] is None
    assert result['bootstrap']['intervals']['slope_minus_xgboost']['lower'] is None
    with pytest.raises(ValueError): report(rows + rows[:1], events)
    with pytest.raises(ValueError): report(rows, events * 2)
    empty = report([], [])
    assert empty['models']['slope']['lead']['recall_ge60'] is None


def test_missing_policy_does_not_fabricate_episodes():
    rows, events = fixture()
    result = forecast_report(rows, events, provenance=PROVENANCE)
    assert result['models']['slope']['lead']['ge60'] is None
    assert result['bootstrap']['intervals']['ltfp_graph_minus_xgboost']['missing_reason'] == 'episodes-not-ready'


@pytest.mark.parametrize('mode', ['empty', 'all-unavailable', 'partial', 'gap', 'clock', 'full', 'undeclared'])
def test_exposure_requires_complete_scoring(mode):
    rows, _ = fixture((60,))
    for row in rows:
        row['risk_label'] = None
    truth = [dict(run_id='0', service='s', group_id='0', outcome='no-breach',
                  evidence_id='independent-fixture', healthy_hours=2 / 60,
                  score_schedule=dict(start='2026-01-01T00:05:00Z', stop='2026-01-01T00:07:00Z',
                                      cadence_seconds=60, cutoff_delay_seconds=30))]
    if mode == 'empty':
        rows = []
        truth[0]['healthy_hours'] = 2
        truth[0]['score_schedule']['stop'] = '2026-01-01T02:05:00Z'
    elif mode == 'all-unavailable':
        for row in rows: row['score'] = None
    elif mode == 'partial':
        rows = rows[:4]
    elif mode == 'gap':
        truth[0]['healthy_hours'] = 3 / 60
        truth[0]['score_schedule']['stop'] = '2026-01-01T00:08:00Z'
        for row in rows[4:]:
            row['window_end'] = '2026-01-01T00:07:00Z'
            row['prediction_cutoff'] = '2026-01-01T00:07:30Z'
    elif mode == 'clock':
        for row in rows: row['prediction_cutoff'] = row['window_end']
    elif mode == 'undeclared':
        del truth[0]['score_schedule']
    result = report(rows, [], truth=truth)['models']['slope']
    assert result['healthy_exposure']['service_hours'] == truth[0]['healthy_hours']
    if mode == 'full':
        assert result['healthy_exposure']['per_service_hour'] == 30
        assert result['no_breach']['warned_runs'] == 1
        assert result['healthy_exposure']['coverage_complete'] is True
    else:
        assert result['healthy_exposure']['per_service_hour'] is None
        assert result['healthy_exposure']['false_warnings'] is None
        assert result['no_breach']['warnings'] is None
        assert result['no_breach']['warned_runs'] is None
        assert result['healthy_exposure']['missing_reason'] == 'incomplete-scored-exposure'
        assert result['healthy_exposure']['coverage_complete'] is False
    if mode == 'empty':
        assert result['healthy_exposure']['scored_service_hours'] == 0
        assert result['no_breach']['coverage'][0]['expected_windows'] == 120


def test_exposure_schedule_rejects_inconsistent_hours():
    rows, _ = fixture((60,))
    truth = [dict(run_id='0', service='s', group_id='0', outcome='no-breach', evidence_id='fixture',
                  healthy_hours=2, score_schedule=dict(start='2026-01-01T00:05:00Z',
                  stop='2026-01-01T00:07:00Z', cadence_seconds=60, cutoff_delay_seconds=30))]
    with pytest.raises(ValueError, match='inconsistent exposure'):
        report(rows, [], truth=truth)


def test_bootstrap_declares_healthy_only_groups_and_undefined_draws():
    import random

    rows, events = fixture((60,))
    for row in rows:
        if row['model'] == 'ltfp_self_only': row['score'] = None
    # Group 1 has only rows; groups 2..4 have only certified truth.
    healthy_rows, _ = fixture((60,))
    healthy_rows = [{**row, 'run_id': '1', 'group_id': '1', 'risk_label': 0}
                    for row in healthy_rows]
    truth = [dict(run_id=str(i), service='s', group_id=str(i), outcome='no-breach',
                  evidence_id='healthy-fixture') for i in range(2, 5)]
    result = report(rows + healthy_rows, events, truth=truth)
    bootstrap = result['bootstrap']
    assert bootstrap['group_ids'] == ['0', '1', '2', '3', '4']
    assert bootstrap['groups'] == 5 and bootstrap['event_groups'] == 1
    rng = random.Random(42)
    undefined = sum('0' not in [rng.choice(bootstrap['group_ids']) for _ in range(5)]
                    for _ in range(1000))
    assert 0 < undefined < 1000
    for interval in bootstrap['intervals'].values():
        assert interval['undefined_replicates'] == undefined
        assert interval['valid_replicates'] == 1000 - undefined
    graph_self = bootstrap['intervals']['ltfp_graph_minus_ltfp_self_only']
    assert graph_self['lower'] == graph_self['upper'] == 1
    assert bootstrap['intervals']['ltfp_graph_minus_xgboost']['lower'] == 0
    assert report(list(reversed(rows + healthy_rows)), events, truth=list(reversed(truth))) == result
    no_events = report(healthy_rows, [], truth=truth + [dict(run_id='5', service='s',
        group_id='5', outcome='no-breach', evidence_id='healthy-fixture')])['bootstrap']
    assert no_events['intervals']['slope_minus_xgboost']['lower'] is None
    assert no_events['intervals']['slope_minus_xgboost']['undefined_replicates'] == 1000
    assert no_events['intervals']['slope_minus_xgboost']['missing_reason'] == 'no-event-bearing-replicates'
