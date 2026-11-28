import math
from sys import float_info

import pytest

from sentinelops.ml.model_benchmark_detector_combo import COMBO_SELECTION_RULE_ID, calibrate_or_combo, combo_score


def _ledger(negatives, positives, *, baseline_count=100):
    anchors = [(0, 0, 0)] * (baseline_count - len(negatives))
    anchors += [(0, mad, ads) for mad, ads in negatives] + [(1, mad, ads) for mad, ads in positives]
    return {model: [dict(record_id=str(index), eligible=True, risk_label=row[0], score=row[column],
                         missing_reason='missing' if row[column] is None else None)
                    for index, row in enumerate(anchors)]
            for column, model in enumerate(('median_mad', 'adsketch'), 1)}


def test_exhaustive_grid_can_raise_mad_threshold_to_add_adsketch_only_positive():
    ledger = _ledger([(4, 0), (0, 6)], [(3, 12), (5, 0), (0, 6)])
    expected = {'status': 'ready', 'missing_reason': None, 'threshold': 1.0, 'budget': .01,
                'thresholds': {'median_mad': 5, 'adsketch': 6},
                'negative_flags': 1, 'negatives': 100, 'positive_flags': 3, 'positives': 3,
                'grid_size': 9, 'selection_rule': COMBO_SELECTION_RULE_ID}
    assert calibrate_or_combo(ledger) == expected
    assert calibrate_or_combo({model: reversed(rows) for model, rows in ledger.items()}) == expected


def test_mad_only_wins_when_adsketch_adds_no_positive_and_increases_false_flags():
    point = calibrate_or_combo(_ledger([(0, 6)], [(5, 6)]))
    assert point['thresholds'] == {'median_mad': 5, 'adsketch': None}
    assert (point['positive_flags'], point['negative_flags'], point['grid_size']) == (1, 0, 2)


def test_ties_choose_disabled_adsketch_then_higher_mad_threshold():
    point = calibrate_or_combo(_ledger([(3, 0)], [(5, 7)]))
    assert point['thresholds'] == {'median_mad': 5, 'adsketch': None}
    assert (point['positive_flags'], point['negative_flags'], point['grid_size']) == (1, 0, 4)


def test_no_grid_candidate_meets_a_stricter_budget():
    point = calibrate_or_combo(_ledger([(5, 0)], [(5, 1)]), budget=0)
    assert point['status'] == 'not-ready'
    assert point['missing_reason'] == 'no-combo-thresholds-within-budget'
    assert point['grid_size'] == 2
    assert point['negative_flags'] is None


def test_mad_requires_a_ready_solo_threshold():
    point = calibrate_or_combo(_ledger([(None, 7)], [(5, 7)]))
    assert point['status'] == 'not-ready'
    assert point['missing_reason'] == 'at-least-100-eligible-scored-negatives-required'
    assert point['negatives'] == 100 and point['grid_size'] == 0


def test_mad_only_uses_just_mad_availability_for_its_calibration_denominators():
    point = calibrate_or_combo(_ledger([(None, 0), (0, None)], [(5, 0), (None, 0)], baseline_count=102))
    assert point['thresholds'] == {'median_mad': 5, 'adsketch': None}
    assert (point['negatives'], point['positives'], point['positive_flags']) == (101, 1, 1)


def test_mad_only_can_be_calibrated_when_adsketch_has_no_ready_solo_threshold():
    point = calibrate_or_combo(_ledger([(0, None)], [(5, 10)]))
    assert point['status'] == 'ready'
    assert point['thresholds'] == {'median_mad': 5, 'adsketch': None}


def test_unavailable_components_use_the_other_score_and_unknowns_are_not_counted():
    ledger = _ledger([(None, 4), (5, None)], [(None, 7), (8, None), (None, None)], baseline_count=102)
    for model, rows in ledger.items():
        rows += [dict(record_id='unknown', eligible=True, risk_label=None, score=100, missing_reason=None),
                 dict(record_id='ineligible', eligible=False, risk_label=0, score=100, missing_reason=None)]
    point = calibrate_or_combo(ledger)
    assert (point['negatives'], point['positives'], point['positive_flags'], point['negative_flags']) == (102, 2, 2, 0)
    assert point['thresholds'] == {'median_mad': 8, 'adsketch': 7}


def test_unknown_and_ineligible_scores_cannot_change_the_candidate_grid_or_operating_point():
    ledger = _ledger([(5, 0)], [(0, 0)])
    expected = calibrate_or_combo(ledger)
    assert expected['thresholds'] == {'median_mad': 5, 'adsketch': None}
    assert expected['grid_size'] == 2
    for rows in ledger.values():
        rows.extend([dict(record_id='unknown', eligible=True, risk_label=None, score=999, missing_reason=None),
                     dict(record_id='ineligible', eligible=False, risk_label=0, score=500, missing_reason=None)])
    assert calibrate_or_combo(ledger) == expected


def test_combo_score_uses_availability_and_frozen_points():
    points = {'median_mad': 4, 'adsketch': 6}
    assert combo_score(4, 3, points) == 1
    assert combo_score(None, 7.5, points) == 1.25
    assert combo_score(1, None, points) == .25
    assert combo_score(None, None, points) is None
    assert combo_score(None, 100, {**points, 'adsketch': None}) is None
    assert combo_score(1, 100, {**points, 'adsketch': None}) == .25


def test_combo_threshold_boundary_and_finite_overflow():
    points = {'median_mad': math.ulp(0.0), 'adsketch': None}
    assert combo_score(1.0, None, points) == float_info.max
    assert combo_score(points['median_mad'], None, points) == 1.0
    points['median_mad'] = 4.0
    assert combo_score(math.nextafter(points['median_mad'], 0), None, points) < 1.0


@pytest.mark.parametrize('score', [-1, math.inf, math.nan, True])
def test_combo_score_rejects_invalid_component_scores(score):
    points = {'median_mad': 4, 'adsketch': None}
    with pytest.raises(ValueError, match='nonnegative and finite'):
        combo_score(1, score, points)


@pytest.mark.parametrize('model, threshold', [('median_mad', 0), ('median_mad', math.inf),
                                               ('adsketch', -1), ('adsketch', math.nan), ('adsketch', math.inf)])
def test_combo_score_rejects_invalid_points_even_without_scores(model, threshold):
    points = {'median_mad': 4, 'adsketch': 6, model: threshold}
    with pytest.raises(ValueError, match='thresholds must be positive'):
        combo_score(None, None, points)
