"""Development operating points from caller-certified calibration records only."""
from bisect import bisect_left
import math

from sentinelops.telemetry.core import stable_id


def calibrate(rows, *, calibration_identity):
    """Rows: record_id, eligible(bool), risk_label(0/1/None), score, missing_reason.

    The caller certifies calibration membership and independent negative windows.
    Record IDs detect duplicates; they cannot establish statistical independence.
    UNKNOWN/ineligible records never become negative observations. Missing scores
    require a nonblank reason. No threshold here is a frozen real-data artifact.
    """
    if not isinstance(calibration_identity, str) or not calibration_identity.strip():
        raise ValueError('calibration identity required')
    ids, certified = set(), []
    missing = skipped = unknown = 0
    canonical = []
    for row in rows:
        key, eligible, label, score = (row[name] for name in ('record_id', 'eligible', 'risk_label', 'score'))
        reason = row['missing_reason']
        if not isinstance(key, str) or not key.strip() or key in ids:
            raise ValueError('unique calibration record IDs required')
        ids.add(key)
        if type(eligible) is not bool or (label is not None and (type(label) is not int or label not in (0, 1))):
            raise ValueError('invalid eligibility or target; UNKNOWN must be None')
        if score is None:
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError('missing score requires a reason')
            missing += 1
        elif type(score) not in (int, float) or not math.isfinite(score) or reason is not None:
            raise ValueError('finite score without missing reason required')
        canonical.append(dict(record_id=key, eligible=eligible, risk_label=label, score=score, missing_reason=reason))
        if not eligible:
            skipped += 1
        elif label is None:
            unknown += 1
        elif score is not None:
            certified.append((score, label))
    negatives = sorted(score for score, label in certified if label == 0)
    positives = [score for score, label in certified if label == 1]
    result = dict(status='not-ready', missing_reason='at-least-100-eligible-scored-negatives-required',
                  threshold=None, comparison='>=', budget=.01, negative_flags=None,
                  negative_windows=len(negatives),
                  eligible_negative_windows=sum(row['eligible'] and row['risk_label'] == 0 for row in canonical),
                  positive_windows=len(positives), positive_flags=None,
                  sensitivity=None, rows=len(canonical), missing_scores=missing, skipped_windows=skipped,
                  unknown_windows=unknown, calibration_identity=calibration_identity,
                  identity_sha256=stable_id({'calibration_identity': calibration_identity,
                                            'rows': sorted(canonical, key=lambda row: row['record_id'])}),
                  above_observed_maximum=False,
                  fixed_slope_baseline={'threshold': .05, 'comparison': '>', 'kind': 'fixed-uncalibrated-baseline'})
    if len(negatives) < 100:
        return result
    # Integer arithmetic avoids rounding the exact 1% flag budget upward.
    allowed = len(negatives) // 100
    candidates = sorted({score for score, _ in certified})
    threshold = next((value for value in candidates if len(negatives) -
                     bisect_left(negatives, value) <= allowed), None)
    fallback = threshold is None
    if fallback:
        threshold = math.nextafter(candidates[-1], math.inf)
        if not math.isfinite(threshold):
            result.update(missing_reason='no-finite-threshold', above_observed_maximum=True)
            return result
    positive_flags = sum(score >= threshold for score in positives)
    result.update(status='ready', missing_reason=None, threshold=threshold,
                  negative_flags=sum(score >= threshold for score in negatives),
                  positive_flags=positive_flags,
                  sensitivity=positive_flags / len(positives) if positives else None,
                  above_observed_maximum=fallback)
    return result
