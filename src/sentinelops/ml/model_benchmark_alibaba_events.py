"""Oracle and event projection for Alibaba proxy ranking, including causal warmup.

``proxy_minute_labels`` uses exactly the per-minute oracle in the Lane B fit's
``project_detector_run``: latency strictly above 2.0 times the positive median
of up to 60 preceding contiguous minutes, with at least five history values.
Missing latency or a gap resets history. These are per-minute labels, rather
than the OR-window calibration anchors emitted by ``_calibration_ledger``.
"""

from collections import defaultdict, deque
from datetime import timedelta
from itertools import groupby
from statistics import median

from sentinelops.contracts import utc_time
from sentinelops.ml.alibaba_training import split_of
from sentinelops.ml.model_benchmark_detector_evaluation import KEY_FIELDS
from sentinelops.ml.model_benchmark_detector_fit import (
    BASELINE_LOOKBACK, BASELINE_MINIMUM, BUCKET_SECONDS, PROXY_MULTIPLIER, _ordered_minutes,
)
from sentinelops.telemetry.core import stable_id, timestamp


ALIBABA_EVENT_RULE = {
    'version': 'alibaba-proxy-event/v1',
    'dataset': 'alibaba-proxy',
    'service_split': 'evaluation',
    'relative_minutes_inclusive': [324, 352],
    'warmup_start_minute': 259,
    'bucket_seconds': BUCKET_SECONDS,
    'oracle': {'multiplier': PROXY_MULTIPLIER, 'comparison': '>',
               'baseline': 'median-of-preceding-contiguous-minutes',
               'lookback_minutes': BASELINE_LOOKBACK, 'minimum_history_minutes': BASELINE_MINIMUM,
               'positive_baseline_required': True, 'gap_or_missing_latency': 'reset-history'},
    'positive_label': 1,
    'negative_label': 0,
    'unknown_label': None,
    'maximum_merge_negative_minutes': 4,
    'minimum_negative_history_minutes': 5,
    'deadline_seconds': 300,
    'healthy_exclusion_before_seconds': 300,
    'healthy_exclusion_after_seconds': 300,
    'ineligible_alert_censor': 'onset-through-end-plus-deadline-inclusive',
    'reason_precedence': ['onset-before-scoring-range', 'insufficient-pre-onset-history',
                          'unknown-pre-onset-history', 'insufficient-follow-up'],
}
ALIBABA_EVENT_RULE_ID = stable_id(ALIBABA_EVENT_RULE)
_DATASET = ALIBABA_EVENT_RULE['dataset']
_MINUTE = timedelta(seconds=ALIBABA_EVENT_RULE['bucket_seconds'])
_FIRST_MINUTE, _LAST_MINUTE = ALIBABA_EVENT_RULE['relative_minutes_inclusive']
_HISTORY = ALIBABA_EVENT_RULE['minimum_negative_history_minutes']
_DEADLINE = timedelta(seconds=ALIBABA_EVENT_RULE['deadline_seconds'])
_EXCLUSION_BEFORE = timedelta(seconds=ALIBABA_EVENT_RULE['healthy_exclusion_before_seconds'])
_EXCLUSION_AFTER = timedelta(seconds=ALIBABA_EVENT_RULE['healthy_exclusion_after_seconds'])


def proxy_minute_labels(minutes) -> list[dict]:
    """Return service/timestamp/y dictionaries for validated ``DetectorMinute`` inputs.

    Timestamps are integer epoch seconds, as in the fit. Results are sorted by
    service and timestamp; each label depends only on earlier latency values.
    """
    labelled = []
    for service, service_minutes in groupby(_ordered_minutes(minutes), key=lambda minute: minute.service):
        history = deque(maxlen=BASELINE_LOOKBACK)
        previous = None
        for minute in service_minutes:
            if previous is None or minute.timestamp - previous != BUCKET_SECONDS or minute.latency_ms is None:
                history.clear()
            label = None
            if minute.latency_ms is not None:
                if len(history) >= BASELINE_MINIMUM:
                    baseline = median(history)
                    if baseline > 0:
                        label = int(minute.latency_ms > PROXY_MULTIPLIER * baseline)
                history.append(minute.latency_ms)
            labelled.append({'service': service, 'timestamp': minute.timestamp, 'y': label})
            previous = minute.timestamp
    return labelled


def project_alibaba_proxy_events(windows) -> tuple[list[dict], set[tuple]]:
    """Return ordered events and healthy KEY_FIELDS tuples for a scoring timeline.

    Every row must carry an integer ``relative_minute`` and identify the same
    UTC minute-zero origin. Missing coordinates raise; eligibility and observed
    row boundaries never determine the scoring epoch. The CLI can materialize
    these coordinates from an explicit ``--minute-zero`` before calling here.
    Warmup rows contribute oracle history and events while remaining ineligible.
    """
    labels_by_service = defaultdict(dict)
    negatives = defaultdict(dict)
    bounds = set()
    for row in windows:
        key = tuple(row[field] for field in KEY_FIELDS)
        dataset, group, run, service, bucket_end = key
        if (dataset != _DATASET or not isinstance(service, str) or not service.strip()
                or group != service or run != service):
            raise ValueError('Alibaba event windows require Alibaba proxy and group_id = run_id = service')
        if split_of(service) != 'evaluation':
            raise ValueError('Alibaba event projection requires evaluation services')
        end = utc_time(bucket_end)
        if end.second or end.microsecond:
            raise ValueError('Alibaba event bucket must close on a UTC minute')
        label = row['y']
        if label is not None and (type(label) is not int or label not in (0, 1)):
            raise ValueError('Alibaba event label must be 0, 1 or null')
        if end in labels_by_service[service]:
            raise ValueError('duplicate Alibaba event service minute')
        if type(row['eligible']) is not bool:
            raise ValueError('Alibaba event window eligibility must be boolean')
        labels_by_service[service][end] = label
        if label == 0:
            negatives[service][end] = (*key[:-1], timestamp(end))
        if 'relative_minute' not in row:
            raise ValueError('relative_minute required on every Alibaba event window')
        minute = row['relative_minute']
        if type(minute) is not int or minute < ALIBABA_EVENT_RULE['warmup_start_minute']:
            raise ValueError('Alibaba event relative minute must be at least 259')
        if minute < _FIRST_MINUTE and row['eligible']:
            raise ValueError('Alibaba warmup windows must be ineligible')
        bounds.add(end - minute * _MINUTE)

    if len(bounds) > 1:
        raise ValueError('Alibaba event windows must share one relative-minute origin')
    if not bounds:
        return [], set()
    origin = next(iter(bounds))
    first, last = origin + _FIRST_MINUTE * _MINUTE, origin + _LAST_MINUTE * _MINUTE

    events, healthy_keys = [], set()
    for service, labels in sorted(labels_by_service.items()):
        segments = []
        for positive in sorted(end for end, label in labels.items() if label == 1):
            if positive > last:
                continue
            if segments:
                onset, end = segments[-1]
                steps = (positive - end) // _MINUTE
                if (steps <= ALIBABA_EVENT_RULE['maximum_merge_negative_minutes'] + 1
                        and all(labels.get(end + offset * _MINUTE) == 0 for offset in range(1, steps))):
                    segments[-1] = onset, positive
                    continue
            segments.append((positive, positive))

        healthy = {end: key for end, key in negatives[service].items() if first <= end <= last}
        for onset, end in segments:
            history = [labels.get(onset - offset * _MINUTE) for offset in range(1, _HISTORY + 1)]
            reason = None
            if onset < first:
                reason = 'onset-before-scoring-range'
            elif onset - _HISTORY * _MINUTE < min(labels):
                reason = 'insufficient-pre-onset-history'
            elif None in history:
                reason = 'unknown-pre-onset-history'
            elif any(history):
                reason = 'insufficient-pre-onset-history'
            elif onset + _DEADLINE > last:
                reason = 'insufficient-follow-up'

            onset_at = timestamp(onset)
            event_id = stable_id({'rule_id': ALIBABA_EVENT_RULE_ID, 'service': service, 'onset_at': onset_at})
            events.append({'event_id': event_id, 'dataset': _DATASET, 'group_id': service, 'run_id': service,
                           'service': service, 'kind': _DATASET, 'onset_at': onset_at, 'confirmed_at': onset_at,
                           'eligible': reason is None, 'reason': reason, 'end_at': timestamp(end),
                           'length_minutes': (end - onset) // _MINUTE + 1,
                           'positive_minutes': sum(label == 1 for bucket, label in labels.items() if onset <= bucket <= end),
                           'censor_until': timestamp(end + _DEADLINE) if reason is not None else None})
            excluded_start = onset - _EXCLUSION_BEFORE
            excluded_steps = (end + _EXCLUSION_AFTER - excluded_start) // _MINUTE + 1
            for offset in range(excluded_steps):
                healthy.pop(excluded_start + offset * _MINUTE, None)
        healthy_keys.update(healthy.values())
    return events, healthy_keys
