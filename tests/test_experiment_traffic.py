from copy import deepcopy

import pytest

from sentinelops.experiments.traffic import server_log_coverage,s5_expected_responses


def blocking_inventory_logs():
    outcomes=[{'request_entry_service':service,'trace_id':trace,'correlation_id':trace}
              for service,trace in [('checkout-service','checkout'),('payment-service','direct-pay'),('inventory-service','direct-inv')]]
    starts=[];completed=[]
    for service,trace,path,route in [('checkout-service','checkout','/v1/checkout','/v1/checkout'),
        ('inventory-service','checkout','/v1/inventory/sku-001','/v1/inventory/{sku}'),
        ('payment-service','direct-pay','/v1/payments/charge','/v1/payments/charge'),
        ('inventory-service','direct-inv','/v1/inventory/sku-001','/v1/inventory/{sku}')]:
        row={'service':service,'trace_id':trace,'span_id':service+trace,'request_instance_id':service+trace,
             'correlation_id':trace,'method':'GET' if service=='inventory-service' else 'POST','path':path}
        starts.append({**row,'event':'request_started'})
        completed.append({**row,'event':'request_completed','route':route,'status_code':503})
    return starts,completed,outcomes


def test_blocking_inventory_uses_observed_starts_not_one_payment_per_checkout():
    starts,completed,outcomes=blocking_inventory_logs()
    coverage=server_log_coverage(starts,completed,outcomes)
    assert all(row['complete'] for row in coverage.values())
    assert coverage['checkout-service']['observed_starts']==1
    assert coverage['inventory-service']['observed_starts']==2
    assert coverage['payment-service']['observed_starts']==1
    assert server_log_coverage(starts+deepcopy(starts),completed+deepcopy(completed),outcomes)==coverage


def test_missing_all_top_level_service_logs_does_not_turn_into_complete_zero_traffic():
    starts,completed,outcomes=blocking_inventory_logs()
    starts=[row for row in starts if row['service']!='payment-service']
    completed=[row for row in completed if row['service']!='payment-service']
    coverage=server_log_coverage(starts,completed,outcomes)
    assert not coverage['payment-service']['complete']
    assert coverage['payment-service']['top_level_requests']==1


def test_missing_completion_and_forged_request_identity_fail_completeness():
    starts,completed,outcomes=blocking_inventory_logs()
    coverage=server_log_coverage(starts,completed[:-1],outcomes)
    assert not coverage['inventory-service']['complete']
    completed[0]['span_id']='another-span'
    with pytest.raises(ValueError,match='matching observed request start'):
        server_log_coverage(starts,completed,outcomes)


def test_direct_503_is_expected_only_during_the_declared_error_fault():
    outcomes=[{'request_entry_service':service,'phase':'fault','status':status} for service,status in
              [('checkout-service',502),('payment-service',503),('inventory-service',503)]]
    assert s5_expected_responses(outcomes,'error')
    assert not s5_expected_responses(outcomes,'latency')
    outcomes[1]['phase']='recovery'
    assert not s5_expected_responses(outcomes,'error')
