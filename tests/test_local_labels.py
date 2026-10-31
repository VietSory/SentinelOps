"""Synthetic oracle derivations test software; they grant no corpus admission."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from sentinelops.experiments.local_labels import oracle_from_requests, window_labels
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp
from sentinelops.experiments.traffic import PATHS

START = datetime(2026, 10, 31, tzinfo=timezone.utc)


def fixture_oracle(minutes=14):
    opened, completed, clients = [], [], []
    for minute in range(minutes):
        for index in range(30):
            for entry in BUSINESS_ROUTES:
                trace = f'{len(clients)+1:032x}'
                correlation = 'fixture-' + trace
                instant = START + timedelta(minutes=minute, seconds=index+1)
                clients.append({'request_entry_service':entry, 'trace_id':trace, 'correlation_id':correlation,
                    'started_at':timestamp(instant), 'completed_at':timestamp(instant+timedelta(milliseconds=20)),
                    'client_duration_ms':20., 'status':200})
                for service in BUSINESS_ROUTES if entry == 'checkout-service' else (entry,):
                    identity = {'service':service, 'request_instance_id':trace+service, 'trace_id':trace,
                                'span_id':f'{len(opened)+1:016x}', 'correlation_id':correlation}
                    opened.append({**identity,'event':'request_started','method':BUSINESS_ROUTES[service][0],
                                   'path':PATHS[service],'timestamp_unix_ms':int(instant.timestamp()*1000)+1})
                    completed.append({**identity,'event':'request_completed','method':BUSINESS_ROUTES[service][0],
                        'route':BUSINESS_ROUTES[service][1], 'timestamp_unix_ms':int(instant.timestamp()*1000)+11,
                        'status_code':200,'duration_ms':10.})
    return opened, completed, clients


def derive(data):
    return oracle_from_requests(*data, started_at=timestamp(START), ended_at=timestamp(START+timedelta(minutes=14)))


def test_exact_request_oracle_survives_json_roundtrip_and_certifies_observed_health():
    oracle = json.loads(json.dumps(derive(fixture_oracle())))
    frame = {'service':'payment-service','window_start':timestamp(START),'window_end':timestamp(START+timedelta(minutes=5)),
             'as_of':timestamp(START+timedelta(minutes=5,seconds=30))}
    labels = window_labels(frame, oracle, gradual_run=False, healthy_reference_run=True)
    assert labels['healthy'] and labels['risk_label'] == 0
    assert oracle['missing_is_healthy'] is False


@pytest.mark.parametrize('missing', ['client','start','completion','traffic'])
def test_missing_oracle_does_not_become_healthy(missing):
    opened, completed, clients = fixture_oracle()
    if missing == 'client':
        clients[0].pop('client_duration_ms')
        with pytest.raises(ValueError, match='elapsed-time'): derive((opened,completed,clients))
        return
    if missing == 'start': opened.pop(0)
    if missing == 'completion': completed.pop(0)
    if missing == 'traffic':
        opened, completed, clients = [], [], []
    oracle = derive((opened, completed, clients))
    frame = {'service':'payment-service','window_start':timestamp(START),'window_end':timestamp(START+timedelta(minutes=5)),
             'as_of':timestamp(START+timedelta(minutes=5,seconds=30))}
    labels = window_labels(frame, oracle, gradual_run=False, healthy_reference_run=True)
    assert not labels['healthy'] and labels['risk_label'] is None


def test_forged_status_or_clock_is_not_a_healthy_certificate():
    data = fixture_oracle()
    data[2][0]['status'] = 503
    with pytest.raises(ValueError, match='status mismatch'): derive(data)
    data = fixture_oracle()
    data[2][0]['client_duration_ms'] = 1.
    with pytest.raises(ValueError, match='cannot cover observed server work'): derive(data)


def test_verified_future_onset_needs_observed_gradual_prefix_and_exact_horizon():
    oracle = derive(fixture_oracle())
    service = oracle['services']['payment-service']
    service['first_violation_at'] = timestamp(START+timedelta(minutes=8))
    service['confirmed_at'] = timestamp(START+timedelta(minutes=10))
    for minute, value in ((2,100.),(3,200.),(4,300.)):
        service['buckets'][str(int(START.timestamp())+minute*60)]['p95_latency_ms'] = value
    frame = {'service':'payment-service','window_start':timestamp(START),'window_end':timestamp(START+timedelta(minutes=5)),
             'as_of':timestamp(START+timedelta(minutes=5,seconds=30))}
    result = window_labels(frame, oracle, gradual_run=True)
    assert result['risk_label'] == 1 and not result['healthy']
    assert window_labels(frame, oracle, gradual_run=False, healthy_reference_run=True)['risk_label'] is None
    service['buckets'][str(int(START.timestamp())+4*60)]['p95_latency_ms'] = 100.
    assert window_labels(frame, oracle, gradual_run=True)['risk_label'] is None
    with pytest.raises(ValueError, match='300-second'): window_labels(frame, oracle, gradual_run=True, horizon_seconds=60)


def test_future_labels_are_censored_without_full_confirmation_tail():
    oracle = derive(fixture_oracle())
    frame = {'service':'payment-service','window_start':timestamp(START+timedelta(minutes=7)),
             'window_end':timestamp(START+timedelta(minutes=12)),'as_of':timestamp(START+timedelta(minutes=12,seconds=30))}
    result = window_labels(frame, oracle, gradual_run=False, healthy_reference_run=True)
    assert result['healthy'] and result['risk_label'] is None


def test_negative_label_needs_both_confirmation_buckets_at_exact_horizon():
    oracle = derive(fixture_oracle())
    frame = {'service':'payment-service','window_start':timestamp(START),'window_end':timestamp(START+timedelta(minutes=5)),
             'as_of':timestamp(START+timedelta(minutes=5,seconds=30))}
    # Possible onset at minute10 requires observation through minute12, even
    # though its first violating bucket would already be complete at minute11.
    oracle['ended_at'] = timestamp(START+timedelta(minutes=11))
    assert window_labels(frame, oracle, gradual_run=False,healthy_reference_run=True)['risk_label'] is None
    oracle['ended_at'] = timestamp(START+timedelta(minutes=12))
    assert window_labels(frame, oracle, gradual_run=False,healthy_reference_run=True)['risk_label'] == 0
    frame['as_of'] = timestamp(START+timedelta(minutes=6))
    result = window_labels(frame, oracle, gradual_run=False,healthy_reference_run=True)
    assert result['risk_label'] is None and result['risk_missing_reason'] == 'forecast-anchor-minute-advanced'
