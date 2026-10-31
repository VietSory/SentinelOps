"""Independent request/SLO evidence for the approved local corpus.

No model score or intervention timestamp establishes health or an outcome here.
These pure derivations do not admit a source or authorize fitting by themselves.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
import re

from sentinelops.contracts import utc_time
from sentinelops.experiments.labels import SLOConfig, measure_slo
from sentinelops.experiments.traffic import PATHS
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def oracle_from_requests(full_records, completions, outcomes, *, started_at, ended_at, slo=SLOConfig()):
    """Reconcile unsampled server completion truth with an independent client ledger.

    Missing evidence makes affected full buckets UNKNOWN. Conflicting identities,
    statuses or invalid clocks reject the source. No absent error line becomes 0.
    """
    slo.validate()
    begin, finish = utc_time(started_at), utc_time(ended_at)
    if finish <= begin:
        raise ValueError('oracle requires a completed positive capture interval')
    clients, starts, ends = {}, {}, {}
    unknown = set()
    direct = defaultdict(int)
    for row in outcomes:
        entry, trace, correlation = (row.get(k) for k in ('request_entry_service', 'trace_id', 'correlation_id'))
        if entry not in BUSINESS_ROUTES or not isinstance(trace, str) or not re.fullmatch('[0-9a-f]{32}', trace) or not correlation:
            raise ValueError('client oracle lacks native request identity')
        start, end = utc_time(row['started_at']), utc_time(row['completed_at'])
        if not begin <= start <= end <= finish or not _number(row.get('client_duration_ms')):
            raise ValueError('client oracle lacks prospective elapsed-time/source bounds')
        # UTC and monotonic clocks are sampled separately in the producer. Do
        # not claim an exact cross-clock equality or turn scheduling delay into
        # a source-clock conversion. UTC bounds and observed server work are
        # checked independently below; client duration is never a model input.
        status = row['status']
        if not (type(status) is int and 100 <= status <= 599 or status == 'transport_error'):
            raise ValueError('client oracle status has unsupported semantics')
        key = (trace, correlation)
        if key in clients:
            raise ValueError('duplicate client attempt identity')
        clients[key] = row

    def server_fact(row):
        service = row.get('service')
        if service not in BUSINESS_ROUTES:
            raise ValueError('foreign server identity in business oracle')
        identity = tuple(row.get(k) for k in ('trace_id','span_id','correlation_id','request_instance_id'))
        if (any(not isinstance(v, str) or not v for v in identity)
            or not re.fullmatch('[0-9a-f]{32}', identity[0]) or not re.fullmatch('[0-9a-f]{16}', identity[1])
            or type(row.get('timestamp_unix_ms')) is not int):
            raise ValueError('server oracle request identity/clock differs')
        if (identity[0], identity[2]) not in clients:
            raise ValueError('business server traffic is outside the independent client ledger')
        return (service, identity[3]), identity

    for row in full_records:
        service = row.get('service')
        if (service not in BUSINESS_ROUTES or row.get('event') != 'request_started'
            or (row.get('method'), row.get('path')) != (BUSINESS_ROUTES[service][0], PATHS[service])):
            continue
        key, identity = server_fact(row)
        fact = (identity, row['timestamp_unix_ms'])
        if key in starts and starts[key] != fact:
            raise ValueError('conflicting duplicate server start')
        starts[key] = fact
    by_trace = defaultdict(dict)
    for row in completions:
        service = row.get('service')
        if service not in BUSINESS_ROUTES or row.get('event') != 'request_completed':
            continue
        if (row.get('method'), row.get('route')) != BUSINESS_ROUTES[service]:
            raise ValueError('server completion is not the approved business route')
        key, identity = server_fact(row)
        if not _number(row.get('duration_ms')) or type(row.get('status_code')) is not int or not 100 <= row['status_code'] <= 599:
            raise ValueError('server oracle duration/status differs')
        fact = (identity, row['timestamp_unix_ms'], row['status_code'], row['duration_ms'])
        if key in ends:
            if ends[key][0] != fact:
                raise ValueError('conflicting duplicate server completion')
            continue
        ends[key] = (fact, row)
        trace_key = (identity[0], identity[2])
        if service in by_trace[trace_key]:
            raise ValueError('multiple business server requests impersonate one native service/trace')
        by_trace[trace_key][service] = key

    def mark_unknown(client):
        services = BUSINESS_ROUTES if client['request_entry_service'] == 'checkout-service' else (client['request_entry_service'],)
        first = int(utc_time(client['started_at']).timestamp()) // 60 * 60
        last = int(utc_time(client['completed_at']).timestamp()) // 60 * 60
        for bucket in range(first, last+1, 60):
            unknown.update((service, bucket) for service in services)

    for trace_key, client in clients.items():
        entry = client['request_entry_service']
        linked = by_trace[trace_key]
        expected = set(BUSINESS_ROUTES) if entry == 'checkout-service' and client['status'] == 200 else {entry}
        valid = expected <= linked.keys() and client['status'] != 'transport_error'
        if valid:
            top = ends[linked[entry]][1]
            if top['status_code'] != client['status']:
                raise ValueError('independent client/server response status mismatch')
            if client['client_duration_ms'] + 1 < top['duration_ms']:
                raise ValueError('client elapsed time cannot cover observed server work')
        lower = utc_time(client['started_at']).timestamp()*1000
        upper = utc_time(client['completed_at']).timestamp()*1000
        for service, key in linked.items():
            fact, row = ends[key]
            if not lower-1 <= fact[1] <= upper+1:
                raise ValueError('server completion escapes its independent client clock bounds')
            opened = starts.get(key)
            if opened is None:
                valid = False
                continue
            if opened[0] != fact[0] or not lower-1 <= opened[1] <= fact[1] <= upper+1:
                raise ValueError('server identity/timing escapes its independent client attempt')
        if not valid:
            mark_unknown(client)
        else:
            direct[entry, ends[linked[entry]][1]['timestamp_unix_ms']//60000*60] += 1
    for key, (identity, _) in starts.items():
        if key not in ends:
            mark_unknown(clients[identity[0], identity[2]])

    result = {}
    first = math.ceil(begin.timestamp()/60)*60
    last = math.floor(finish.timestamp()/60)*60
    for service in BUSINESS_ROUTES:
        grid = {}
        valid_records = []
        for bucket in range(first, last, 60):
            complete = (service, bucket) not in unknown and direct[service, bucket] >= slo.minimum_requests
            grid[str(bucket)] = {'availability': 'VERIFIED' if complete else 'UNKNOWN',
                            'direct_client_count': direct[service, bucket], 'breach': None,
                            'p95_latency_ms': None, 'error_rate': None}
        for (observed_service, _), (_, row) in ends.items():
            bucket = row['timestamp_unix_ms']//60000*60
            if observed_service == service and grid.get(str(bucket), {}).get('availability') == 'VERIFIED':
                valid_records.append(row)
        measured = measure_slo(valid_records, service, slo, start_at=begin, end_at=finish)
        for row in measured['buckets']:
            bucket = int(utc_time(row['bucket_start']).timestamp())
            if not row['eligible']:
                grid[str(bucket)]['availability'] = 'UNKNOWN'
            else:
                grid[str(bucket)].update(breach=row['breach'], p95_latency_ms=row['p95_latency_ms'], error_rate=row['error_rate'])
        result[service] = {'buckets': grid, 'first_violation_at': measured['first_violation_at'],
                           'confirmed_at': measured['confirmed_at']}
    return {'oracle_version': 'server-completion-client-ledger/v1', 'started_at': started_at, 'ended_at': ended_at,
            'client_attempts': len(clients), 'server_starts': len(starts), 'server_completions': len(ends),
            'services': result, 'missing_is_healthy': False}


def window_labels(frame, oracle, *, gradual_run, healthy_reference_run=False, horizon_seconds=300):
    """Attach evaluator labels only after a causal feature frame is frozen."""
    if horizon_seconds != 300:
        raise ValueError('ADR-014 requires the frozen 300-second risk horizon')
    start, end, cutoff = (utc_time(frame[k]) for k in ('window_start','window_end','as_of'))
    service = oracle['services'][frame['service']]
    buckets = service['buckets']
    history = [buckets.get(str(int(start.timestamp()) + i*60)) for i in range(5)]
    verified = all(b and b['availability'] == 'VERIFIED' for b in history)
    # Only prospective healthy runs can supply the healthy reference, and only
    # when independent observed activity/SLO evidence verifies the entire window.
    healthy = bool(healthy_reference_run and verified and all(b['breach'] is False for b in history))
    onset = utc_time(service['first_violation_at']) if service['first_violation_at'] else None
    risk, reason = None, 'insufficient-future-oracle'
    # The legacy risk payload identifies its closed feature-window anchor. SLO
    # onsets are aligned to 60-second bucket starts. A source receipt within that
    # same minute admits the identical set of future onsets; a later minute does
    # not, so stale risk targets remain unknown instead of being backdated.
    future = end + timedelta(seconds=horizon_seconds)
    tail = math.floor(future.timestamp()/60)*60 + SLOConfig().persistence*60
    future_keys = range(math.floor(cutoff.timestamp()/60)*60, tail, 60)
    future_complete = (utc_time(oracle['ended_at']).timestamp() >= tail and
        all(buckets.get(str(k), {}).get('availability') == 'VERIFIED' for k in future_keys))
    prefix = [b['p95_latency_ms'] for b in history[-3:]] if verified else []
    observed_trend = bool(len(prefix) == 3 and all(a < b for a,b in zip(prefix, prefix[1:])))
    if cutoff >= end + timedelta(seconds=60):
        reason = 'forecast-anchor-minute-advanced'
    elif not verified:
        reason = 'incomplete-history-oracle'
    elif onset is not None and cutoff >= onset:
        reason = 'incident-or-post-incident'
    elif onset is not None and cutoff < onset <= future:
        if gradual_run and observed_trend:
            risk, reason = 1, None
        else:
            reason = 'no-verified-pre-onset-gradual-trend'
    elif future_complete:
        risk, reason = 0, None
    return {'healthy': healthy, 'risk_label': risk, 'risk_missing_reason': reason,
            'prediction_cutoff': frame['as_of'], 'slo_onset_at': service['first_violation_at'],
            'risk_forecast_anchor_at':frame['window_end'],
            'gradual_prefix_observed': observed_trend, 'future_confirmation_tail': timestamp(datetime.fromtimestamp(tail, timezone.utc))}
