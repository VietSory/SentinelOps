"""Joint calibration and frozen scoring for the Lane B ADSketch/MAD OR."""
import math
from fractions import Fraction
from itertools import product
from sys import float_info

from sentinelops.ml.model_benchmark_calibration import calibrate
from sentinelops.telemetry.core import stable_id


COMBO_MODEL = 'adsketch_mad_or'
KEY_FIELDS = ('dataset', 'group_id', 'run_id', 'service', 'bucket_end')
_COMPONENTS = ('median_mad', 'adsketch')
COMPONENT_AVAILABILITIES = ('none', 'median_mad_only', 'adsketch_only', 'both')
COMBO_SELECTION_RULE_ID = stable_id({
    'version': 'adsketch-mad-or-calibration/v2',
    'grid': 'solo-1-percent-plus-all-scores-at-or-above-solo;adsketch-may-be-disabled',
    'comparison': '>=',
    'availability': 'either-enabled-component-scored',
    'minimum_available_negatives': 100,
    'negative_budget': 'negatives//100',
    'order': ['max-positive-flags', 'min-negative-flags', 'max-adsketch-threshold', 'max-mad-threshold'],
})


def _valid_score(score):
    return type(score) in (int, float) and math.isfinite(score) and score >= 0


def _component_thresholds(thresholds):
    if set(thresholds) != set(_COMPONENTS):
        raise ValueError('combo requires both component thresholds')
    mad, ads = (thresholds[model] for model in _COMPONENTS)
    if (not _valid_score(mad) or mad == 0
            or ads is not None and (not _valid_score(ads) or ads == 0)):
        raise ValueError('combo thresholds must be positive and finite or ADSketch may be disabled')
    return mad, ads


def combo_score(s_mad, s_ads, thresholds):
    """Return the maximum normalized score among enabled, scored components."""
    points = _component_thresholds(thresholds)
    scores = s_mad, s_ads
    if any(score is not None and not _valid_score(score) for score in scores):
        raise ValueError('combo component scores must be nonnegative and finite')
    return max((min(score / point, float_info.max) for score, point in zip(scores, points)
                if score is not None and point is not None), default=None)


def append_combo_predictions(predictions, operating_point):
    """Append deterministic combo rows to base predictions for scoring and replay."""
    if operating_point.get('status') != 'ready' or operating_point.get('threshold') != 1.0:
        raise ValueError('combo predictions require a ready operating point fixed at 1.0')
    thresholds = operating_point['thresholds']
    _component_thresholds(thresholds)
    rows, grouped = list(predictions), {}
    for row in rows:
        key = tuple(row[field] for field in KEY_FIELDS)
        model = row['model_id']
        components = grouped.setdefault(key, {})
        if model == COMBO_MODEL or model in components:
            raise ValueError('combo derivation requires unique base predictions')
        components[model] = row
    for components in grouped.values():
        mad, ads = (components.get(model, {}).get('score') for model in _COMPONENTS)
        score = combo_score(mad, ads, thresholds)
        availability = COMPONENT_AVAILABILITIES[int(mad is not None) + 2 * int(ads is not None)]
        template = next(iter(components.values()))
        reasons = {row.get('reason') for row in components.values()}
        unavailable = next(iter(reasons)) if len(reasons) == 1 else None
        row = {field: template[field] for field in KEY_FIELDS}
        for field in ('available_at', 'decision_at', 'clock', 'eligible', 'eligibility_reason', 'y',
                      'label_reason', 'healthy', 'raw_ref', 'oracle_ref'):
            if field in template:
                row[field] = template[field]
        rows.append({**row, 'model_id': COMBO_MODEL, 'score': score, 'component_availability': availability,
                     'status': 'available' if score is not None else 'unavailable',
                     'reason': (unavailable or 'no-available-component-score') if score is None else None,
                     'pattern_kind': None, 'pattern_id': None, 'inference_seconds': None,
                     'graph_neighbor_count': None})
    return rows


def _grid(indexed, anchor_ids, solo, *, allow_disabled):
    candidates = {solo, *(indexed[anchor]['score'] for anchor in anchor_ids
                          if indexed[anchor]['score'] is not None and indexed[anchor]['score'] >= solo)}
    if allow_disabled:
        candidates.add(math.inf)
    observations = sorted(((indexed[anchor]['score'], index, indexed[anchor]['risk_label'])
                           for index, anchor in enumerate(anchor_ids)
                           if indexed[anchor]['score'] is not None and indexed[anchor]['score'] >= solo),
                          reverse=True)
    cursor = negative_mask = positive_mask = 0
    for point in sorted(candidates, reverse=True):
        while cursor < len(observations) and observations[cursor][0] >= point:
            _, index, label = observations[cursor]
            if label == 0:
                negative_mask |= 1 << index
            else:
                positive_mask |= 1 << index
            cursor += 1
        yield point, negative_mask, positive_mask


def calibrate_or_combo(ledger_rows, *, budget=0.01):
    """Search matched component calibration ledgers under a joint negative flag budget."""
    if (type(budget) not in (int, float) or not math.isfinite(budget) or not 0 <= budget <= .01):
        raise ValueError('combo negative flag budget must be between zero and one percent')
    if not set(_COMPONENTS) <= set(ledger_rows):
        raise ValueError('combo requires both component calibration ledgers')
    indexed, solo = {}, {}
    for model in _COMPONENTS:
        rows = list(ledger_rows[model])
        solo[model] = calibrate(rows, calibration_identity=model)
        if any(row['score'] is not None and not _valid_score(row['score']) for row in rows):
            raise ValueError('combo component scores must be nonnegative and finite')
        indexed[model] = {row['record_id']: row for row in rows}
    mad_rows, ads_rows = (indexed[model] for model in _COMPONENTS)
    if (mad_rows.keys() != ads_rows.keys() or any(
            (row['eligible'], row['risk_label']) != (ads_rows[key]['eligible'], ads_rows[key]['risk_label'])
            for key, row in mad_rows.items())):
        raise ValueError('combo calibration models must share a timeline and labels')
    anchor_ids = sorted(key for key, row in mad_rows.items()
                        if row['eligible'] and row['risk_label'] is not None
                        and (row['score'] is not None or ads_rows[key]['score'] is not None))
    def counts(available):
        negatives = sum(mad_rows[key]['risk_label'] == 0 for key in available)
        return negatives, len(available) - negatives

    union_counts = counts(anchor_ids)
    mad_counts = counts([key for key in anchor_ids if mad_rows[key]['score'] is not None])
    negatives, positives = union_counts
    result = dict(status='not-ready', missing_reason=None, threshold=1.0, budget=budget,
                  thresholds=dict.fromkeys(_COMPONENTS), negatives=negatives, positives=positives,
                  negative_flags=None, positive_flags=None, grid_size=0, selection_rule=COMBO_SELECTION_RULE_ID)
    if solo['median_mad']['status'] != 'ready':
        return {**result, 'missing_reason': solo['median_mad']['missing_reason']}
    solo_points = {model: point['threshold'] for model, point in solo.items()}
    _component_thresholds(solo_points)
    mad_grid = list(_grid(mad_rows, anchor_ids, solo_points['median_mad'], allow_disabled=False))
    ads_grid = list(_grid(ads_rows, anchor_ids, solo_points['adsketch'], allow_disabled=True)
                    if solo_points['adsketch'] is not None else [(math.inf, 0, 0)])
    result['grid_size'] = len(mad_grid) * len(ads_grid)
    budgets = {mode: count[0] // 100 if budget == .01 else math.floor(count[0] * Fraction(str(budget)))
               for mode, count in ((True, mad_counts), (False, union_counts))}
    best = None
    for (mad, mad_neg, mad_pos), (ads, ads_neg, ads_pos) in product(mad_grid, ads_grid):
        negative_flags = (mad_neg | ads_neg).bit_count()
        if negative_flags <= budgets[ads == math.inf]:
            rank = (mad_pos | ads_pos).bit_count(), -negative_flags, ads, mad
            if best is None or rank > best:
                best = rank
    if best is None:
        return {**result, 'missing_reason': 'no-combo-thresholds-within-budget'}
    positive_flags, negative_rank, ads, mad = best
    negatives, positives = mad_counts if ads == math.inf else union_counts
    return {**result, 'status': 'ready', 'negative_flags': -negative_rank, 'positive_flags': positive_flags,
            'negatives': negatives, 'positives': positives,
            'thresholds': {'median_mad': mad, 'adsketch': ads if math.isfinite(ads) else None}}
