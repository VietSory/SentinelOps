"""Count observed request identities without assuming every Checkout visits Payment."""
from collections import Counter,defaultdict

from sentinelops.telemetry.core import BUSINESS_ROUTES

PATHS={"checkout-service":"/v1/checkout", "payment-service":"/v1/payments/charge",
       "inventory-service":"/v1/inventory/sku-001"}


def s5_expected_responses(outcomes,fault_mode):
    if not outcomes:return False
    for row in outcomes:
        entry=row.get('request_entry_service');phase=row.get('phase')
        if entry not in BUSINESS_ROUTES or phase not in ('warmup','fault','recovery'):return False
        expected=200
        if phase=='fault' and fault_mode=='error':expected=502 if entry=='checkout-service' else 503
        if row.get('status')!=expected:return False
    return True


def server_log_coverage(full_records,serving_records,outcomes):
    if any(row.get('request_entry_service') not in BUSINESS_ROUTES or any(not isinstance(row.get(key),str) or not row[key]
           for key in ('trace_id','correlation_id')) for row in outcomes):
        raise ValueError('invalid top-level business request provenance')
    routed={(row['request_entry_service'],row['trace_id'],row['correlation_id']) for row in outcomes}
    correlations={row['correlation_id'] for row in outcomes}
    requests=Counter(row['request_entry_service'] for row in outcomes)
    started={};completed={};direct=defaultdict(set)
    def identity(row):
        values=tuple(row.get(key) for key in ('request_instance_id','trace_id','span_id','correlation_id'))
        if any(not isinstance(value,str) or not value for value in values):
            raise ValueError('server log coverage requires request/trace/span/correlation identities')
        return values
    for row in full_records:
        service=row.get('service')
        if (service not in BUSINESS_ROUTES or row.get('event')!='request_started'
            or row.get('correlation_id') not in correlations
            or (row.get('method'),row.get('path'))!=(BUSINESS_ROUTES[service][0],PATHS[service])):continue
        fact=identity(row);key=(service,fact[0])
        if key in started and started[key]!=fact:raise ValueError('conflicting server request-start identity')
        started[key]=fact
        if (service,fact[1],fact[3]) in routed:direct[service].add(fact[1])
    for row in serving_records:
        service=row.get('service')
        if service not in BUSINESS_ROUTES or row.get('event')!='request_completed':continue
        if (row.get('method'),row.get('route'))!=BUSINESS_ROUTES[service]:continue
        fact=identity(row);key=(service,fact[0])
        if started.get(key)!=fact:raise ValueError('completion lacks its matching observed request start')
        completed[key]=fact
    result={}
    for service in sorted(BUSINESS_ROUTES):
        expected=sum(key[0]==service for key in started)
        observed=sum(key[0]==service for key in completed)
        result[service]={'observed_starts':expected,'matched_completions':observed,
            'top_level_requests':requests[service],'top_level_logged_starts':len(direct[service]),
            'complete':expected>0 and observed>=.95*expected and len(direct[service])>=.95*requests[service]}
    return result
