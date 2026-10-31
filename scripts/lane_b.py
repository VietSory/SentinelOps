"""Isolated local B infrastructure and foreground API/worker launchers.

No access to A's corpus, process lifecycle or published ports. Volumes survive
down. Existing worker guards remain unchanged; workers run on the local host.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
from urllib.parse import urlsplit

PROJECT = Path(__file__).resolve().parents[1]
NAME = 'sentinelops-b'
ROOT = PROJECT/'artifacts/lane-b'
PORTS = {
    'prometheus':{19090:9090},'otel-collector':{14317:4317,14318:4318,18889:8889,23133:13133},
    'jaeger':{26686:16686},'postgres':{15432:5432},'control-postgres':{15433:5432},
    'checkout-service':{18001:8000},'payment-service':{18002:8000},'inventory-service':{18003:8000}}


def compose(*args):
    return ['docker','compose','-p',NAME,'-f',str(PROJECT/'docker-compose.yml'),
            '-f',str(PROJECT/'docker-compose.control.yml'),'-f',str(PROJECT/'docker-compose.b.yml'),*args]


def docker_environment():
    # Parent Compose variables must not redirect this launcher to another stack.
    return {k:v for k,v in os.environ.items() if not k.startswith('COMPOSE_')}


def validate_config(config):
    if config.get('name') != NAME or set(config['services']) != set(PORTS):
        raise ValueError('lane B requires its exact owned project and services')
    published = []
    for service, expected in PORTS.items():
        actual = {}
        for port in config['services'][service].get('ports',[]):
            if port.get('host_ip') != '127.0.0.1' or port.get('protocol','tcp') != 'tcp':
                raise ValueError('lane B published ports must be selected TCP loopback ports')
            public = int(port['published'])
            if public in actual:
                raise ValueError('duplicate lane B published port')
            actual[public] = int(port['target'])
            published.append(public)
        if actual != expected:
            raise ValueError('lane B port isolation differs for '+service)
    if len(set(published)) != len(published):
        raise ValueError('lane B services cannot share a host port')
    return config


def require_owned_containers():
    identifiers = subprocess.check_output(['docker','ps','-aq','--filter',
        'label=com.docker.compose.project='+NAME],text=True).split()
    for identifier in identifiers:
        # Only read ownership metadata, never the container environment.
        labels = json.loads(subprocess.check_output(['docker','inspect','--format','{{json .Config.Labels}}',identifier],text=True))
        owner = labels.get('com.docker.compose.project.working_dir')
        if (labels.get('com.docker.compose.project') != NAME
            or labels.get('com.docker.compose.service') not in PORTS
            or not isinstance(owner,str) or not owner
            or Path(owner).resolve() != PROJECT):
            raise ValueError('refuse to mutate a lane B project owned by another checkout')


def init_runtime():
    ROOT.mkdir(parents=True,exist_ok=True)
    marker = ROOT/'runtime.json'
    identity = {'runtime_version':'lane-b/local-v1','compose_project':NAME,'checkout':str(PROJECT),'root':str(ROOT)}
    if marker.exists():
        if json.loads(marker.read_text()) != identity:
            raise ValueError('lane B runtime root belongs to another checkout/project')
    else:
        if list(ROOT.iterdir()):
            raise ValueError('refuse to appropriate a nonempty unregistered runtime root')
        with marker.open('x') as stream: json.dump(identity,stream)
    token = ROOT/'api-token'
    if not token.exists():
        fd = os.open(token,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as stream: stream.write(secrets.token_urlsafe(48)+'\n')
    if token.is_symlink() or not stat.S_ISREG(token.stat().st_mode) or token.stat().st_mode & 0o077:
        raise ValueError('lane B API token must be a private regular file')
    return identity


def runtime_environment(model_store=None, release_ref=None):
    init_runtime()
    control = os.environ.get('CONTROL_DATABASE_URL',
        'postgresql://control_writer:local-control-writer@127.0.0.1:15433/sentinelops_control')
    parsed = urlsplit(control)
    if (parsed.scheme not in ('postgres','postgresql') or parsed.hostname != '127.0.0.1' or parsed.port != 15433
        or parsed.username != 'control_writer' or parsed.path != '/sentinelops_control' or parsed.query or parsed.fragment):
        raise ValueError('lane B requires its explicit loopback control_writer database on 15433')
    environment = {k:v for k,v in docker_environment().items()
                   if k not in ('SENTINELOPS_MODEL_STORE','SENTINELOPS_MODEL_RELEASE_REF','DATABASE_URL')}
    if bool(model_store) != bool(release_ref):
        raise ValueError('model activation requires both explicitly selected H1 store and release reference')
    if model_store:
        selected = Path(model_store).resolve()
        if not selected.is_relative_to(ROOT.resolve()/'models'):
            raise ValueError('H1 models must be materialized inside the owned B models directory')
        # Existing ModelRegistry performs checksum/admission/validation checks at
        # API/worker startup. Wiring never writes an approval or a release ledger.
        environment.update(SENTINELOPS_MODEL_STORE=str(selected),SENTINELOPS_MODEL_RELEASE_REF=release_ref)
    return {**environment,'CONTROL_DATABASE_URL':control,
        'COMMERCE_EVIDENCE_DATABASE_URL':'postgresql://sentinel:sentinel@127.0.0.1:15432/sentinelops',
        'SENTINELOPS_ENV':'local','SENTINELOPS_ENABLE_DEMO':'false',
        'SENTINELOPS_API_TOKEN':os.environ.get('SENTINELOPS_API_TOKEN') or (ROOT/'api-token').read_text().strip()}


def launch_arguments(command, stream=None, model_store=None, release_ref=None):
    if command == 'serve':
        return [sys.executable,'-m','uvicorn','sentinelops.main:app','--app-dir',str(PROJECT/'src'),
                '--host','127.0.0.1','--port','18000']
    shared = ['--prometheus','http://127.0.0.1:19090','--metrics-otlp-endpoint','http://127.0.0.1:14317']
    if command == 'health':
        return [sys.executable,'-m','scripts.run_health_worker','--store',str(ROOT/'health'),*shared]
    if not stream:
        raise ValueError('incident launcher needs the exact READY stream_id from the health worker')
    models = ['--model-store',str(model_store),'--model-release-ref',release_ref] if model_store and release_ref else []
    return [sys.executable,'-m','scripts.run_incident_worker','--health-stream',stream,
        '--source-store',str(ROOT/'health'),'--store',str(ROOT/'incidents'),'--project',NAME,
        '--jaeger','http://127.0.0.1:26686',*shared,*models]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['config','up','down','status','snapshot','serve','health','incidents'])
    parser.add_argument('--health-stream')
    parser.add_argument('--model-store',type=Path,help='Explicit H1 materialized trusted store inside artifacts/lane-b/models/')
    parser.add_argument('--model-release-ref',help='Separately accepted immutable H1 release; no automatic activation')
    parser.add_argument('--deployment-ref',help='Explicit immutable local deployment lock; up only, no build/pull fallback')
    args = parser.parse_args()
    if (args.model_store or args.model_release_ref) and args.command not in ('serve','incidents'):
        parser.error('model configuration is only valid for API/incident processes')
    if args.deployment_ref and args.command != 'up': parser.error('deployment lock is only valid with up')
    environment = runtime_environment(args.model_store,args.model_release_ref) if args.command in ('serve','health','incidents') else None
    if args.command == 'serve':
        os.chdir(PROJECT)
        os.execvpe(sys.executable,launch_arguments('serve'),environment)
        return
    config = validate_config(json.loads(subprocess.check_output(compose('config','--format','json'),
                                                               cwd=PROJECT,env=docker_environment(),text=True)))
    if args.command == 'config':
        print(json.dumps({'project':config['name'],'ports':PORTS,'api_port':18000},indent=2))
    elif args.command == 'snapshot':
        from scripts.lane_b_release import snapshot
        init_runtime()
        print(json.dumps({'deployment_ref':snapshot(config,project=PROJECT,root=ROOT),
                          'model_activation_allowed':False,'remediation_approval':False}))
    elif args.command in ('up','down','status'):
        require_owned_containers()
        init_runtime()
        action = {'up':['up','--build','-d','--wait','--wait-timeout','180'],
                  'down':['down'],'status':['ps']}[args.command]
        command = compose(*action)
        if args.deployment_ref:
            from scripts.lane_b_release import materialize
            override = materialize(config,args.deployment_ref,project=PROJECT,root=ROOT)
            command = compose('-f',str(override),'up','-d','--wait','--wait-timeout','180')
            validate_config(json.loads(subprocess.check_output(compose('-f',str(override),'config','--format','json'),
                                               cwd=PROJECT,env=docker_environment(),text=True)))
        subprocess.run(command,cwd=PROJECT,env=docker_environment(),check=True)
    else:
        require_owned_containers()
        os.chdir(PROJECT)
        os.execvpe(sys.executable,launch_arguments(args.command,args.health_stream,args.model_store,args.model_release_ref),environment)


if __name__ == '__main__':
    main()
