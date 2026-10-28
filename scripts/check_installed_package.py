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
from sentinelops.experiments.multifault import grouping_report
from sentinelops.public_data.downloads import fetch_file
from sentinelops.public_data.audit import load_verified_manifest
from sentinelops.public_data.gamma import load_gamma_manifest
from sentinelops.public_data.gamma_audit import audit_gamma
from sentinelops.public_data.resources import resource_window
from sentinelops.public_data.phases import parse_phases
from sentinelops.public_data.gamma_archive import RangeReader
from sentinelops.public_data.cohort import load_cohort_manifest
from sentinelops.public_data.admission import admit_case, session_groups
from sentinelops.public_data.dataset import build_grouped_dataset
from sentinelops.public_data.rcaeval_partitions import group_metadata, require_cases
from sentinelops.contracts import load_schema, validate_payload
from fastapi.testclient import TestClient
for name in ('telemetry', 'feature-window', 'detection-result', 'degradation-risk',
             'incident-evidence-record', 'explanation', 'remediation-action',
             'observation-envelope', 'feature-envelope', 'infrastructure-observation',
             'experiment-run', 'dataset-manifest', 'incident-write',
             'incident-envelope', 'incident-list', 'api-error', 'training-data-policy',
             'research-resource-window', 'research-interference-label', 'health-signal', 'correlation-result',
             'rca-policy', 'rca-result'):
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
client = TestClient(app)
assert client.get('/health').status_code == 200
payload = {'schema_version':'1.0', 'service':'payment-service',
    'window_start':'2026-10-25T00:00:00Z', 'window_end':'2026-10-25T00:05:00Z',
    'features':{'latency': 100}}
assert client.post('/v1/detect', json=payload).status_code == 503
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
