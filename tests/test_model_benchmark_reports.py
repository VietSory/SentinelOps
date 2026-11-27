from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
pytest.importorskip("sklearn")

from sentinelops.ml.model_benchmark_forecast import MODELS
from sentinelops.ml.model_benchmark_reports import forecast_report
from sentinelops.ml.model_benchmark_reports import summarize_sealed_forecasts

PROVENANCE = dict(code='fixture', config='fixture', data='fixture', models='fixture')
THRESHOLDS = {model: dict(watch=.4, degrading=.7) for model in MODELS}


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
