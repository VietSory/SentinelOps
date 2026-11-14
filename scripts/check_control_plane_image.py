"""Exercise the installed API image in disposable, isolated Docker containers.

No credentials/model/data are baked into the image. Test credentials are private
temporary files. No business workload, database, source parity, model approval or
worker deployment is claimed. The packaged worker CLIs are checked with --help.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import uuid

from sentinelops.remediation.domain import image_digest

PROJECT = Path(__file__).resolve().parents[1]
FORBIDDEN_ENV = {'CONTROL_DATABASE_URL', 'SENTINELOPS_ACTION_READ_DATABASE_URL',
                 'SENTINELOPS_API_TOKEN', 'SENTINELOPS_OPERATOR_TOKEN',
                 'SENTINELOPS_MODEL_STORE', 'SENTINELOPS_MODEL_RELEASE_REF',
                 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN'}

PROBE = r'''
import importlib.metadata,json,os,sys,time,urllib.request,urllib.error
from pathlib import Path
import numpy,pandas,pyarrow,sklearn,xgboost,sentinelops
from sentinelops.contracts import load_schema
assert os.getuid()==10001
assert Path(sentinelops.__file__).resolve().is_relative_to(Path('/opt/venv'))
assert not Path('/opt/sentinelops/src').exists()
assert not any(Path('/opt/sentinelops',p).exists() for p in ('artifacts','models','data','.env','.aws'))
load_schema('incident-evidence-record.schema.json')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def request(path, token=None, body=None):
    headers={'Content-Type':'application/json'}
    if token: headers['Authorization']='Bearer '+token
    req=urllib.request.Request('http://127.0.0.1:8000'+path,headers=headers,
        data=None if body is None else json.dumps(body).encode())
    try:
        response=opener.open(req,timeout=2)
    except urllib.error.HTTPError as exc:
        response=exc
    with response:
        content=response.read()
        return response.status,content
deadline=time.monotonic()+60
while True:
    try:
        assert request('/health')[0]==200
        break
    except (OSError,AssertionError):
        if time.monotonic()>deadline: raise
        time.sleep(.25)
assert request('/ui/')[0]==request('/ui/assets/app.js')[0]==200
status,body=request('/ready');ready=json.loads(body)
assert status==503 and ready['dependencies']['model'] is False
assert ready['dependencies']['incident_store'] is False and ready['demo_mode'] is False
payload={'schema_version':'1.0','service':'payment-service',
    'window_start':'2026-01-01T00:00:00Z','window_end':'2026-01-01T00:05:00Z','features':{'request_rate':1}}
for endpoint in ('/v1/detect','/v1/risk'):
    status,body=request(endpoint,body=payload)
    assert status==503 and json.loads(body)['code']=='model_unavailable'
if sys.argv[1]=='production':
    assert os.environ['SENTINELOPS_ENV']=='production'
    assert ready['dependencies']['authentication'] is False
    status,body=request('/v1/incidents')
    assert status==503 and json.loads(body)['code']=='authentication_adapter_unavailable'
else:
    token=os.environ['SENTINELOPS_OPERATOR_TOKEN']
    assert ready['dependencies']['authentication'] is True
    assert request('/v1/incidents')[0]==401
    status,body=request('/v1/incidents',token)
    assert status==503 and json.loads(body)['code']=='control_store_unavailable'
    assert request('/v1/incidents',token,body={})[0]==403
print(json.dumps({'status':'PASS','mode':sys.argv[1],'installed_module':str(Path(sentinelops.__file__).resolve()),
    'libraries':{name:importlib.metadata.version(name) for name in
        ('numpy','pandas','pyarrow','scikit-learn','xgboost-cpu')},
    'model_available':False,'demo_mode':False,'protected_store_unavailable':True,'ui_assets_served':True}))
'''


def command(*args, timeout=120):
    try:
        result = subprocess.run(list(args), check=True, capture_output=True, text=True, timeout=timeout, cwd=PROJECT)
    except subprocess.CalledProcessError as exc:
        # Build/probe output is needed to diagnose CI failures. Runtime token
        # values are never command arguments or part of the probe output.
        detail = ((exc.stdout or '') + '\n' + (exc.stderr or ''))[-8000:]
        raise RuntimeError(f'{args[0]} exited {exc.returncode}:\n{detail}') from None
    return result.stdout.strip()


def immutable_image(value):
    if not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
        image_digest(value)
    return value


def check(image):
    immutable_image(image)
    details = json.loads(command('docker', 'image', 'inspect', image))[0]
    config = details['Config']
    environment = dict(item.split('=', 1) for item in config['Env'])
    assert not FORBIDDEN_ENV.intersection(environment), 'image contains runtime credentials or model selection'
    assert environment.get('SENTINELOPS_ENV') == 'production'
    assert environment.get('SENTINELOPS_ENABLE_DEMO') == 'false'
    assert config['User'] == '10001:10001'
    results = []
    for mode in ('production', 'local-no-model'):
        with tempfile.TemporaryDirectory(prefix='sentinelops-image-check-') as private:
            env_file = Path(private) / 'environment'
            values = '' if mode == 'production' else ('SENTINELOPS_ENV=test\nSENTINELOPS_API_TOKEN=' +
                secrets.token_urlsafe(48) + '\nSENTINELOPS_OPERATOR_TOKEN=' + secrets.token_urlsafe(48) + '\n')
            env_file.touch(mode=0o600)
            env_file.write_text(values)
            name = 'sentinelops-control-image-' + uuid.uuid4().hex[:12]
            container = command('docker', 'run', '-d', '--name', name, '--label', 'sentinelops.check=control-plane-image',
                '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=64m',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '64',
                '--memory', '768m', '--cpus', '1', '--env-file', str(env_file), details['Id'])
            try:
                result = json.loads(command('docker', 'exec', container, 'python', '-c', PROBE, mode))
                for worker in ('scripts.run_health_worker', 'scripts.run_incident_worker'):
                    command('docker', 'exec', container, 'python', '-m', worker, '--help')
                results.append(result)
            finally:
                command('docker', 'rm', '-f', container)
    return {'status': 'PASS', 'scope': 'isolated-installed-image-software-only', 'image_id': details['Id'],
        'source_revision': config.get('Labels', {}).get('org.opencontainers.image.revision'),
        'cases': results, 'network': 'none', 'read_only_root': True, 'uid': 10001,
        'worker_cli_help': True, 'worker_cloud_deployment_verified': False,
        'source_parity_accepted': False, 'model_activation_performed': False, 'research_quality_claim': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--build', action='store_true')
    selection.add_argument('--image', help='Immutable image ID or registry digest, never a tag')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('verification output must be new')
    if args.build:
        if command('git', 'status', '--porcelain'):
            raise ValueError('commit source before building a versioned image')
        revision = command('git', 'rev-parse', 'HEAD')
        tag = 'sentinelops-control-plane-check:' + uuid.uuid4().hex[:12]
        command('docker', 'build', '-f', 'docker/Dockerfile.control-plane',
                '--build-arg', 'SENTINELOPS_SOURCE_REVISION=' + revision, '-t', tag, '.', timeout=900)
        image = command('docker', 'image', 'inspect', '--format', '{{.Id}}', tag)
    else:
        image = immutable_image(args.image)
    result = check(image)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'image_id': result['image_id'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
