"""Build/install a wheel outside the checkout and exercise its runtime contracts."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CHECK = r'''
import sys
import json
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sentinelops
assert Path(sentinelops.__file__).resolve().is_relative_to(Path(sys.argv[1]))
from sentinelops.main import app
from sentinelops.api import create_app
from sentinelops.api_settings import Settings
from sentinelops.incidents.repository import IncidentRepository
from sentinelops.incidents.health import advance_health
from sentinelops.incidents.health_replay import replay_health_manifest
from sentinelops.incidents.correlation import correlate_health
from sentinelops.incidents.correlation_replay import replay_correlation
from sentinelops.incidents.rca import evaluate_rca
from sentinelops.incidents.rca_replay import replay_rca
from sentinelops.incidents.rca_producer import prepare_intent, publish_intent
from sentinelops.experiments.runner import ExperimentConfig
from sentinelops.experiments.local_corpus import acquisition_lock
from sentinelops.experiments.local_campaign import planned_slots
from sentinelops.experiments.local_labels import oracle_from_requests
from sentinelops.ml.local_data import audit_local_corpus
from sentinelops.experiments.multifault import grouping_report
from sentinelops.public_data.downloads import fetch_file
from sentinelops.public_data.audit import load_verified_manifest
from sentinelops.public_data.gamma import load_gamma_manifest
from sentinelops.public_data.gamma_audit import audit_gamma
from sentinelops.public_data.resources import resource_window
from sentinelops.public_data.phases import parse_phases
from sentinelops.public_data.gamma_archive import RangeReader
from sentinelops.public_data.gamma_clients import summarize_log, summarize_integers
from sentinelops.public_data.cohort import load_cohort_manifest
from sentinelops.public_data.admission import admit_case, session_groups
from sentinelops.public_data.dataset import build_grouped_dataset
from sentinelops.public_data.rcaeval_partitions import group_metadata, require_cases
from sentinelops.public_data.rcaeval_native import adapt_native_case, load_native_inputs, validate_profile
from sentinelops.public_data.rcaeval_evaluation import evaluate_manifest
from sentinelops.incidents.native_rca import evaluate_native
from sentinelops.workers.health import HealthWorker, latest_bucket
from sentinelops.workers.health_source import HealthLedgerReader
from sentinelops.workers.incident_state import advance_groups
from sentinelops.workers.incident_evidence import NativeIncidentEvidence, completion_records
from sentinelops.workers.incident_rca import derive_continuous
from sentinelops.workers.incident_journal import IncidentJournal
from sentinelops.workers.incident import IncidentWorker
from sentinelops.workers.incident_replay import replay as replay_incident_ledger
from sentinelops.ml.registry import ModelRegistry
from sentinelops.ml.readiness import audit_readiness
from sentinelops.workers.model_signals import collect_model_signals
from sentinelops.public_data.rcaeval_evaluation import diagnose_native
from sentinelops.remediation.verification import verify_post_action_health
from sentinelops.contracts import load_schema, validate_payload
from fastapi.testclient import TestClient
assert callable(verify_post_action_health)
client_summary = summarize_log(b'10 requests in 1.00m, 1.00MB read\n')
assert client_summary['summary']['duration_seconds'] == '60.00'
assert client_summary['http_5xx_count'] is None and client_summary['socket_errors'] is None
assert summarize_integers(b'123\n456\n')['unit'] is None
for name in ('telemetry', 'feature-window', 'detection-result', 'degradation-risk',
             'incident-evidence-record', 'explanation', 'remediation-action',
             'observation-envelope', 'feature-envelope', 'infrastructure-observation',
             'experiment-run', 'dataset-manifest', 'incident-write',
             'incident-envelope', 'incident-list', 'api-error', 'training-data-policy',
             'research-resource-window', 'research-interference-label', 'health-signal', 'correlation-result',
             'rca-policy', 'rca-result', 'training-data-policy-v2', 'lane-b-deployment-lock'):
    assert load_schema(name + '.schema.json')['type'] == 'object'
assert 'root_causes_ground_truth' in load_schema('experiment-run.schema.json')['properties']
policy={'policy_version':'slo-health/local-v1','latency_ms':750.,'error_rate':.05,
    'minimum_requests':30,'minimum_coverage':.8,'entry_buckets':2,'recovery_buckets':3,'maximum_lag_seconds':60}
health=advance_health(None,None,service='payment-service',environment='test',source_profile='wheel/v1',
    bucket_end=datetime(2026,10,26,tzinfo=timezone.utc),evaluated_at=datetime(2026,10,26,tzinfo=timezone.utc),policy=policy)
validate_payload('health-signal.schema.json',health)
assert health['availability']=='UNKNOWN' and health['health'] is None
correlation=correlate_health([],{'version':1,'dependencies':{'payment-service':[]}},
    as_of=datetime(2026,10,26,tzinfo=timezone.utc),source_scope_sha256='b'*64)
assert correlation['groups']==[] and correlation['actions_allowed'] is False
rca_sample=json.loads(Path(sys.argv[2]).read_text())
validate_payload('rca-result.schema.json',rca_sample)
assert rca_sample['actions_allowed'] is False
from sentinelops.workers.metrics import WorkerMetrics
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
worker_metrics = WorkerMetrics(InMemoryMetricReader(), environment='test', instance='health-0')
worker_metrics.heartbeat(ready=False)
worker_metrics.close()
client = TestClient(app)
assert client.get('/ui/').status_code == 200
assert 'IncidentClient' in client.get('/ui/assets/client.js').text
assert "connect-src 'self'" in client.get('/ui/').headers['content-security-policy']
assert client.get('/health').status_code == 200
payload = {'schema_version':'1.0', 'service':'payment-service',
    'window_start':'2026-10-25T00:00:00Z', 'window_end':'2026-10-25T00:05:00Z',
    'features':{'latency': 100}}
assert client.post('/v1/detect', json=payload).status_code == 503
assert client.post('/v1/risk', json=payload).status_code == 503
assert client.post('/v1/detect', json={}).status_code == 422
assert client.post('/v1/incidents/demo').status_code == 404
demo = TestClient(create_app(Settings(demo_mode=True)))
assert demo.post('/v1/detect', json=payload).headers['x-sentinelops-mode'] == 'demo'
assert demo.post('/v1/incidents/demo').status_code == 200
ier = demo.post('/v1/incidents/demo').json()
write = {'schema_version':'1.0','payload':ier,'context':{
    'availability':'UNKNOWN','recovery_source':None,'source_refs':['fixture://wheel'],
    'config_refs':['config://wheel/v1'],'trigger_sources':[{'source':'manual','signal_id':'wheel',
        'emitted_at':ier['detected_at'],'source_ref':'fixture://wheel'}]},
    'lifecycle_stage':'OPEN','reason':'installed wheel contract reference validation'}
validate_payload('incident-write.schema.json', write)
write['payload']['invented'] = True
try:
    validate_payload('incident-write.schema.json', write)
    raise AssertionError('nested frozen IER schema was not enforced')
except ValueError:
    pass
print('INSTALLED WHEEL SMOKE TEST: PASS')
'''


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sentinelops-wheel-") as temp:
        workspace = Path(temp)
        subprocess.run([
            sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
            "--wheel-dir", str(workspace), str(ROOT),
        ], check=True, cwd=workspace)
        wheel, = workspace.glob("sentinelops-*.whl")
        installed = workspace / "site"
        subprocess.run([
            sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(installed), str(wheel),
        ], check=True, cwd=workspace)
        rca_sample = workspace / "rca-sample.json"
        rca_sample.write_bytes((ROOT / "samples" / "rca-result.sample.json").read_bytes())
        subprocess.run([
            sys.executable, "-I", "-c", CHECK, str(installed), str(rca_sample),
        ], check=True, cwd=workspace)


if __name__ == "__main__":
    main()
