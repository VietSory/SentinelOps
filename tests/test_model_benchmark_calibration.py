"""Synthetic operating-point checks; no real calibration or freeze artifact."""
from copy import deepcopy
import math

import pytest

from sentinelops.ml.model_benchmark_calibration import calibrate


def rows(scores, label=0):
    return [dict(record_id=f'{label}:{i}', eligible=True, risk_label=label,
                 score=score, missing_reason=None) for i, score in enumerate(scores)]


def test_lowest_threshold_with_ties_at_exact_budget_and_no_mutation():
    data = rows([0.] * 98 + [.5, 1.]) + rows([.6, 1.], 1)
    before = deepcopy(data)
    result = calibrate(data, calibration_identity='fixture')
    assert result['status'] == 'ready'
    # .5 flags two negatives, but observed positive score .6 flags just one.
    assert result['threshold'] == .6
    assert (result['negative_flags'], result['negative_windows']) == (1, 100)
    assert result['positive_flags'] == 2 and result['sensitivity'] == 1
    assert calibrate(list(reversed(data)), calibration_identity='fixture') == result
    assert data == before
    assert result['fixed_slope_baseline']['threshold'] == .05


@pytest.mark.parametrize('scores', [[1.] * 100, [0.] * 98 + [1.] * 2])
def test_ties_cannot_be_split_and_fallback_has_zero_flags(scores):
    result = calibrate(rows(scores) + rows([1.], 1), calibration_identity='fixture')
    assert result['above_observed_maximum']
    assert result['threshold'] == math.nextafter(1., math.inf)
    assert result['negative_flags'] == result['positive_flags'] == result['sensitivity'] == 0


@pytest.mark.parametrize('n', [0, 99, 100])
def test_support_boundary(n):
    result = calibrate(rows(list(range(n))), calibration_identity='fixture')
    assert result['status'] == ('ready' if n == 100 else 'not-ready')
    assert result['negative_windows'] == n
    if n < 100: assert result['threshold'] is None and result['negative_flags'] is None
    else: assert result['threshold'] == 99 and result['negative_flags'] == 1


def test_missing_unknown_ineligible_do_not_inflate_negative_support():
    data = rows(list(range(99))) + [
        dict(record_id='missing', eligible=True, risk_label=0, score=None, missing_reason='unavailable'),
        dict(record_id='unknown', eligible=True, risk_label=None, score=1., missing_reason=None),
        dict(record_id='excluded', eligible=False, risk_label=0, score=1., missing_reason=None)]
    result = calibrate(data, calibration_identity='fixture')
    assert result['status'] == 'not-ready'
    assert result['negative_windows'] == 99 and result['eligible_negative_windows'] == 100
    assert (result['missing_scores'], result['unknown_windows'], result['skipped_windows']) == (1, 1, 1)


@pytest.mark.parametrize('change', [dict(score=float('nan')), dict(score=float('inf')),
    dict(score=True), dict(score=None), dict(risk_label='UNKNOWN'), dict(risk_label=False), dict(eligible=1)])
def test_invalid_inputs_rejected(change):
    data = rows([1.])
    data[0].update(change)
    with pytest.raises(ValueError): calibrate(data, calibration_identity='fixture')


def test_duplicate_and_missing_identity_rejected():
    data = rows([1.])
    with pytest.raises(ValueError): calibrate(data * 2, calibration_identity='fixture')
    with pytest.raises(ValueError): calibrate(data, calibration_identity='')


def test_max_float_fallback_is_not_ready_and_strict_json_safe():
    import json
    import sys
    data = rows([sys.float_info.max] * 100) + rows([sys.float_info.max], 1)
    result = calibrate(data, calibration_identity='fixture-max-float')
    assert result['status'] == 'not-ready'
    assert result['missing_reason'] == 'no-finite-threshold'
    assert result['threshold'] is result['negative_flags'] is result['positive_flags'] is result['sensitivity'] is None
    assert result['negative_windows'] == result['eligible_negative_windows'] == 100
    assert result['positive_windows'] == 1
    assert json.loads(json.dumps(result, allow_nan=False)) == result
