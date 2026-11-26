from collections import Counter

import pytest

pytest.importorskip('torch')
pytest.importorskip('xgboost')

from scripts.verify_ltfp_development_support import _require_support


def test_development_support_accepts_isolated_positive_but_enforces_class_and_graph_support():
    train = dict(role='train', positive=3, negative_with_neighbor=55)
    calibration = dict(role='calibration', positive=1, negative_with_neighbor=31, positive_with_neighbor=0)
    fitted = Counter({0: 200, 1: 2})
    scored = {'ltfp_graph': Counter({0: 100, 1: 1})}
    _require_support([train, calibration], fitted, scored)

    with pytest.raises(ValueError, match='usable graph'):
        _require_support([train, {**calibration, 'negative_with_neighbor': 0}], fitted, scored)
    with pytest.raises(ValueError, match='both label'):
        _require_support([train, calibration], Counter({0: 200}), scored)
    for negatives, positives in ((99, 1), (100, 0)):
        with pytest.raises(ValueError, match='at least 100'):
            _require_support([train, calibration], fitted, {'ltfp_graph': Counter({0: negatives, 1: positives})})
