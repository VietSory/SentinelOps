import test from 'node:test';
import assert from 'node:assert/strict';
import {incidentSummaryLines} from '../../src/sentinelops/ui/assets/summary.js';

const latencyIncident = () => ({
  incident_id:'INC-KB1-LIKE',
  affected_services:['checkout-service','payment-service'],
  severity:'high',
  state:'incident',
  root_cause_ranking:[{service:'payment-service',score:.8,evidence_refs:['payment-trace']}],
  service_signals:[],
  evidence:[
    {id:'payment-trace',type:'trace',service:'payment-service',
      fact:'server_duration_ns=955000000; sum_observed_direct_client_duration_ns=5000000; observed_residual_lower_bound_ns=950000000'},
    {id:'db-trace',type:'trace',service:'rds',
      fact:'postgresql_client_duration_ns=5000000; reference_server_duration_ns=960000000'},
    {id:'if-score',type:'model',service:'checkout-service',
      fact:'Validated Isolation Forest inference: anomaly_score=0.99; model_version=fixture'},
    {id:'xgb-score',type:'model',service:'checkout-service',
      fact:'Validated XGBoost risk_score=0.97; state=degrading; model_version=fixture'},
  ],
});

test('KB1-like latency incident is summarized only from structured fields and known evidence facts',() => {
  const lines = incidentSummaryLines(latencyIncident(),{proposals:[]});
  assert.deepEqual(lines,[
    'Dịch vụ bị ảnh hưởng: checkout-service, payment-service',
    'Mức độ/trạng thái: Cao · Sự cố',
    'Nguyên nhân nhiều khả năng: payment-service (điểm 0,80)',
    'Bằng chứng chính: trace cho thấy payment-service xử lý ~955 ms trong khi DB ~5 ms',
    'Model: XGBoost rủi ro 0,97; Isolation Forest bất thường 0,99 (checkout)',
    'Cần engineer xử lý (không có bằng chứng thay đổi deployment để rollback)',
  ]);
});

test('bad-deploy incident reports the loaded pending rollback proposal',() => {
  const payload = latencyIncident();
  payload.evidence.push({id:'change',type:'change',service:'payment-service',
    fact:'API-observed Payment image transition fixture-old -> fixture-new'});
  const proposals = [{status:'pending',proposal:{runbook_id:'payment-bad-deploy-rollback'}}];
  const lines = incidentSummaryLines(payload,{proposals});
  assert.equal(lines.length,6);
  assert.equal(lines.at(-1),'Hướng xử lý: đề xuất rollback chờ duyệt');
});

test('unknown or malformed values are omitted instead of being invented',() => {
  const payload = latencyIncident();
  payload.root_cause_ranking=[];
  payload.service_signals=[];
  payload.evidence=[
    {type:'trace',service:'payment-service',fact:'server_duration_ns=not-a-number'},
    {type:'model',service:'checkout-service',fact:'Validated XGBoost risk_score=unknown'},
  ];
  const lines = incidentSummaryLines(payload,{});
  assert.deepEqual(lines,[
    'Dịch vụ bị ảnh hưởng: checkout-service, payment-service',
    'Mức độ/trạng thái: Cao · Sự cố',
  ]);
  assert.equal(lines.join(' ').includes('NaN'),false);
});
