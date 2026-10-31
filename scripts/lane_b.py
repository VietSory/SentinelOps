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
        if (labels.get('com.docker.compose.project') != NAME
            or labels.get('com.docker.compose.service') not in PORTS
            or Path(labels.get('com.docker.compose.project.working_dir','')).resolve() != PROJECT):
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
    if token.is_symlink() or token.stat().st_mode & 0o077:
        raise ValueError('lane B API token must be a private regular file')
    return identity


def runtime_environment():
    init_runtime()
    control = os.environ.get('CONTROL_DATABASE_URL',
        'postgresql://control_writer:local-control-writer@127.0.0.1:15433/sentinelops_control')
    parsed = urlsplit(control)
    if (parsed.scheme not in ('postgres','postgresql') or parsed.hostname != '127.0.0.1' or parsed.port != 15433
        or parsed.username != 'control_writer' or parsed.path != '/sentinelops_control' or parsed.query or parsed.fragment):
        raise ValueError('lane B requires its explicit loopback control_writer database on 15433')
    environment = {k:v for k,v in docker_environment().items()
                   if k not in ('SENTINELOPS_MODEL_STORE','SENTINELOPS_MODEL_RELEASE_REF','DATABASE_URL')}
    return {**environment,'CONTROL_DATABASE_URL':control,
        'COMMERCE_EVIDENCE_DATABASE_URL':'postgresql://sentinel:sentinel@127.0.0.1:15432/sentinelops',
        'SENTINELOPS_ENV':'local','SENTINELOPS_ENABLE_DEMO':'false',
        'SENTINELOPS_API_TOKEN':os.environ.get('SENTINELOPS_API_TOKEN') or (ROOT/'api-token').read_text().strip()}


def launch_arguments(command, stream=None):
    if command == 'serve':
        return [sys.executable,'-m','uvicorn','sentinelops.main:app','--app-dir',str(PROJECT/'src'),
                '--host','127.0.0.1','--port','18000']
    shared = ['--prometheus','http://127.0.0.1:19090','--metrics-otlp-endpoint','http://127.0.0.1:14317']
    if command == 'health':
        return [sys.executable,'-m','scripts.run_health_worker','--store',str(ROOT/'health'),*shared]
    if not stream:
        raise ValueError('incident launcher needs the exact READY stream_id from the health worker')
    return [sys.executable,'-m','scripts.run_incident_worker','--health-stream',stream,
        '--source-store',str(ROOT/'health'),'--store',str(ROOT/'incidents'),'--project',NAME,
        '--jaeger','http://127.0.0.1:26686',*shared]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['config','up','down','status','serve','health','incidents'])
    parser.add_argument('--health-stream')
    args = parser.parse_args()
    if args.command == 'serve':
        os.chdir(PROJECT)
        os.execvpe(sys.executable,launch_arguments('serve'),runtime_environment())
        return
    config = validate_config(json.loads(subprocess.check_output(compose('config','--format','json'),
                                                               cwd=PROJECT,env=docker_environment(),text=True)))
    if args.command == 'config':
        print(json.dumps({'project':config['name'],'ports':PORTS,'api_port':18000},indent=2))
    elif args.command in ('up','down','status'):
        require_owned_containers()
        init_runtime()
        action = {'up':['up','--build','-d','--wait','--wait-timeout','180'],
                  'down':['down'],'status':['ps']}[args.command]
        subprocess.run(compose(*action),cwd=PROJECT,env=docker_environment(),check=True)
    else:
        require_owned_containers()
        os.chdir(PROJECT)
        os.execvpe(sys.executable,launch_arguments(args.command,args.health_stream),runtime_environment())


if __name__ == '__main__':
    main()
