from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path

import pytest

from sentinelops.experiments.multifault import grouping_report,post_capture_cutoff,selected_development_case,warmup_report
from sentinelops.experiments.labels import SLOConfig
from sentinelops.telemetry.core import stable_id,timestamp
from sentinelops.telemetry.store import ArtifactStore
from test_correlation import END,correlate,opening

ROOT=Path(__file__).resolve().parents[1]
PROTOCOL=json.loads((ROOT/'configs/s5_development_protocol.json').read_text())


def test_two_roots_stay_separate_and_shared_consumer_ambiguity_is_scored_independently():
    result=correlate([opening('payment-service'),opening('inventory-service'),opening('checkout-service')])
    before=stable_id(result)
    report=grouping_report(result,['payment-service','inventory-service'])
    assert report['passed'] and report['missed_roots']==[] and report['false_merge_group_ids']==[]
    assert len(report['ambiguous_consumer_group_ids'])==1
    assert not set(report['root_group_ids']['payment-service'])&set(report['root_group_ids']['inventory-service'])
    changed_truth=grouping_report(result,['payment-service','rds'])
    assert not changed_truth['passed'] and changed_truth['missed_roots']==['rds']
    assert stable_id(result)==before  # Evaluator truth never edits diagnostic outputs.


def test_missing_independent_root_is_a_failed_evaluation_not_an_imputed_diagnosis():
    result=correlate([opening('payment-service'),opening('checkout-service')])
    report=grouping_report(result,['payment-service','inventory-service'])
    assert not report['passed'] and report['missed_roots']==['inventory-service']


def test_post_capture_analysis_uses_operational_receipt_clocks_not_fault_schedule(tmp_path):
    store=ArtifactStore(tmp_path)
    logs=store.write('raw',{'kind':'serving-logs','collected_at':timestamp(END+timedelta(seconds=31))})
    trace=store.write('raw',{'kind':'trace','collected_at':timestamp(END+timedelta(seconds=30))})
    evidence={'logs':logs,'traces':[trace],'infrastructure':[]}
    assert post_capture_cutoff({'as_of':timestamp(END)},store,evidence)==END+timedelta(seconds=31)
    evidence['logs']=store.write('raw',{'kind':'serving-logs'})
    with pytest.raises(ValueError,match='observed receipt'):
        post_capture_cutoff({'as_of':timestamp(END)},store,evidence)


def test_frozen_case_binding_and_closed_holdout_cannot_be_changed_after_outcomes():
    case=PROTOCOL['cases'][0]
    run={'scenario_id':'S5','declared_split':'validation','parameters':{k:v for k,v in case.items() if k!='name'}}
    assert selected_development_case(run,PROTOCOL)==case['name']
    changed=deepcopy(run);changed['parameters']['maximum_delay_ms']+=1
    with pytest.raises(ValueError,match='frozen'):
        selected_development_case(changed,PROTOCOL)
    changed=deepcopy(run);changed['declared_split']='test'
    with pytest.raises(ValueError,match='holdout remains closed'):
        selected_development_case(changed,PROTOCOL)


def test_evaluation_refuses_holdout_metadata_before_loading_its_raw_telemetry(monkeypatch,tmp_path):
    from scripts import evaluate_s5
    pointer={'scenario_id':'S5','declared_split':'test','parameters':{}}
    (tmp_path/'run.json').write_text(json.dumps(pointer))
    def forbidden(path):raise AssertionError('heldout source was opened')
    monkeypatch.setattr(evaluate_s5,'load_run',forbidden)
    with pytest.raises(ValueError,match='holdout remains closed'):
        evaluate_s5.evaluate(tmp_path,tmp_path/'output',ROOT/'configs/s5_development_protocol.json')
    assert not (tmp_path/'output').exists()


def test_warmup_uses_independent_slo_measurements_and_missing_is_not_healthy():
    records=[]
    for service,method,route in [('checkout-service','POST','/v1/checkout'),
        ('payment-service','POST','/v1/payments/charge'),('inventory-service','GET','/v1/inventory/{sku}')]:
        for minute in range(4):
            for i in range(40):records.append({'service':service,'event':'request_completed','method':method,'route':route,
                'request_instance_id':f'{service}-{minute}-{i}','timestamp_unix_ms':int((END.timestamp()+60*minute+i)*1000),
                'status_code':200,'duration_ms':10})
    run={'started_at':timestamp(END),'fault_start_at':timestamp(END+timedelta(minutes=5)),
         'parameters':{'slo':asdict(SLOConfig())}}
    assert all(row['passed'] for row in warmup_report(records,run,3).values())
    for row in records:
        if row['service']=='payment-service':row['duration_ms']=1000
    measured=warmup_report([r for r in records if r['service']!='inventory-service'],run,3)
    assert not measured['payment-service']['passed'] and measured['payment-service']['sustained_breach_at'] is not None
    assert not measured['inventory-service']['passed'] and measured['inventory-service']['eligible_request_buckets']==0
