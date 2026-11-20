"""Trigger one identity-bound OOM demo (256Mi -> 64Mi); never recover it.

The separately deployed memory controller owns on-call approval and 64Mi ->
256Mi recovery. --check performs the same preflight without a PATCH.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import time

NAME, NAMESPACE, CONTAINER = 'sentinelops-oom-demo', 'sentinelops-b-actions', 'oom-demo'
RESOURCES = {'requests': {'cpu': '25m', 'memory': '32Mi'},
             'limits': {'cpu': '250m', 'memory': '256Mi'}}
IMAGE = re.compile(r'[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[a-f0-9]{64}')
LABELS = {'app': NAME, 'sentinelops.io/demo': 'oom'}


class Blocked(ValueError):
    """Precondition failed; no later mutation is allowed."""


class ReadTimeout(Blocked):
    """Both bounded attempts at one read timed out; no mutation was retried."""


def clock():
    return datetime.now(timezone.utc)


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise Blocked('observation timestamp is not timezone aware')
    return parsed


@dataclass(frozen=True)
class Config:
    target: dict
    controller: dict
    kubeconfig: str
    context: str
    lock_file: str = '/tmp/sentinelops-oom-trigger.lock'

    def __post_init__(self):
        if (set(self.target) != {'cluster_uid', 'namespace_uid', 'deployment_uid', 'image',
                                 'namespace', 'deployment', 'container'}
            or self.target['namespace'] != NAMESPACE or self.target['deployment'] != NAME
            or self.target['container'] != CONTAINER or not IMAGE.fullmatch(self.target['image'])
            or any(not isinstance(self.target[k], str) or not 1 <= len(self.target[k]) <= 128
                   for k in ('cluster_uid', 'namespace_uid', 'deployment_uid'))):
            raise Blocked('configuration must bind the dedicated OOM target and pinned image')
        if (set(self.controller) != {'deployment', 'uid', 'configmap', 'key'}
            or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,252}', v)
                   for v in self.controller.values())
            or self.controller['deployment'] == NAME):
            raise Blocked('explicit controller deployment, UID and target ConfigMap binding required')
        if (not Path(self.kubeconfig).is_absolute() or not Path(self.lock_file).is_absolute()
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,252}', self.context)):
            raise Blocked('absolute kubeconfig/lock paths and explicit context required')

    @classmethod
    def from_env(cls):
        path = Path(os.environ.get('DEMO_CONSOLE_OOM_CONFIG', ''))
        if not path.is_absolute() or not path.is_file():
            raise Blocked('DEMO_CONSOLE_OOM_CONFIG must name an existing absolute JSON file')
        value = json.loads(path.read_text())
        if set(value) != {'target', 'controller'}:
            raise Blocked('OOM config requires exactly target and controller objects')
        return cls(value['target'], value['controller'],
                   os.environ.get('DEMO_CONSOLE_KUBECONFIG', ''),
                   os.environ.get('DEMO_CONSOLE_KUBE_CONTEXT', ''),
                   os.environ.get('DEMO_CONSOLE_OOM_LOCK_FILE', '/tmp/sentinelops-oom-trigger.lock'))


class Kubectl:
    def __init__(self, config):
        self.base = ['kubectl', '--kubeconfig', config.kubeconfig, '--context', config.context,
                     '--request-timeout=10s']

    def run(self, args, *, json_result=True, input_json=None):
        command = self.base + args
        read_only = bool(args and args[0] in ('get', 'logs') and input_json is None)
        for attempt in range(2 if read_only else 1):
            print('$ ' + shlex.join(command), flush=True)
            try:
                stdin = ({'input': json.dumps(input_json, separators=(',', ':'))} if input_json is not None
                         else {'stdin': subprocess.DEVNULL})
                result = subprocess.run(command, capture_output=True, text=True, timeout=15,
                                        check=False, **stdin)
            except subprocess.TimeoutExpired as exc:
                if not read_only:
                    raise Blocked('kubectl result UNKNOWN; do not retry a PATCH automatically') from exc
            except OSError as exc:
                raise Blocked('kubectl result unavailable; do not retry a PATCH automatically') from exc
            else:
                # Exec-plugin stderr may contain credentials. Classify locally;
                # print only fixed diagnostics, never provider text.
                print('kubectl exit=' + str(result.returncode), flush=True)
                if not result.returncode:
                    return json.loads(result.stdout) if json_result else result.stdout
                if not read_only or not any(reason in (result.stderr or '').lower() for reason in (
                    'context deadline exceeded', 'i/o timeout', 'tls handshake timeout',
                    'client.timeout exceeded', 'timeout awaiting response headers', 'request timed out',
                    'the server was unable to return a response in the time allotted')):
                    raise Blocked('kubectl request failed; inspect operator diagnostics separately')
            if attempt == 1:
                raise ReadTimeout('Kubernetes read timed out after 2 attempts')
            print('Kubernetes read timed out; retrying once (2/2).', flush=True)
            time.sleep(.25)  # At most 2 x 15s subprocess timeouts + 0.25s backoff.

    def get(self, kind, name=None, *, namespace=NAMESPACE, selector=None):
        args = ['get', kind] + ([name] if name else [])
        if namespace:
            args += ['-n', namespace]
        if selector:
            args += ['-l', selector]
        return self.run(args + ['-o', 'json'])


def ready(deployment):
    status, meta = deployment.get('status', {}), deployment['metadata']
    return (not meta.get('deletionTimestamp') and not deployment['spec'].get('paused', False)
            and deployment['spec'].get('replicas') == 1
            and status.get('observedGeneration') == meta['generation']
            and status.get('replicas') == status.get('readyReplicas') == status.get('updatedReplicas') == 1)


def validate_deployment(config, deployment, *, memory='256Mi'):
    meta, spec = deployment['metadata'], deployment['spec']
    expected = {**RESOURCES, 'limits': {**RESOURCES['limits'], 'memory': memory}}
    containers = spec['template']['spec']['containers']
    if (meta['name'] != NAME or meta['namespace'] != NAMESPACE
        or meta['uid'] != config.target['deployment_uid'] or meta.get('deletionTimestamp')
        or spec.get('paused', False) or spec.get('replicas') != 1
        or spec['selector'] != {'matchLabels': {'app': NAME}}
        or any(spec['template']['metadata']['labels'].get(k) != v for k, v in LABELS.items())
        or len(containers) != 1 or spec['template']['spec'].get('initContainers')
        or containers[0]['name'] != CONTAINER or containers[0]['image'] != config.target['image']
        or containers[0]['resources'] != expected
        or spec['strategy'] != {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 0, 'maxUnavailable': 1}}):
        raise Blocked('dedicated deployment identity, image, resources or rollout policy changed')


def validate_controller(config, controller, cm, latest_log, *, now):
    binding = config.controller
    if (controller['metadata']['uid'] != binding['uid']
        or controller['metadata']['name'] != binding['deployment']
        or controller['metadata']['namespace'] != NAMESPACE or not ready(controller)):
        raise Blocked('bound memory controller is not Ready')
    if json.loads(cm['data'][binding['key']]) != config.target:
        raise Blocked('controller target ConfigMap does not match the configured target')
    pod = controller['spec']['template']['spec']
    matches = []
    for container in pod['containers']:
        argv = container.get('command', []) + container.get('args', [])
        if any(a == 'scripts.run_pod_memory_demo' or a.endswith('/run_pod_memory_demo.py') for a in argv):
            matches.append((container, argv))
    if len(matches) != 1:
        raise Blocked('controller must run the bounded pod memory demo entrypoint')
    container, argv = matches[0]
    if '--policy-enabled' not in argv or '--target' not in argv or not IMAGE.fullmatch(container['image']):
        raise Blocked('memory controller policy or pinned image missing')
    target_path = argv[argv.index('--target') + 1]
    mapped = False
    for mount in container.get('volumeMounts', []):
        if not mount.get('readOnly') or mount.get('subPath'):
            continue
        volume = next((v for v in pod.get('volumes', []) if v['name'] == mount['name']), {})
        source = volume.get('configMap', {})
        if source.get('name') == binding['configmap']:
            paths = [v['path'] for v in source.get('items', []) if v['key'] == binding['key']]
            if 'items' not in source:
                paths = [binding['key']]
            mapped |= any(str(Path(mount['mountPath']) / path) == target_path for path in paths)
    if not mapped:
        raise Blocked('controller target argument is not mapped to the bound ConfigMap')
    # The worker emits one JSON state every two seconds. An active/old proposal
    # must finish before another trigger. Readiness alone cannot establish this.
    latest_phase = None
    # SDK/exporter diagnostics can interleave with the controller's JSON. Only
    # ignore non-phase diagnostics: the latest controller phase (including an
    # error, pending proposal or stale record) must still fail closed.
    for line in latest_log.splitlines()[-30:]:
        at, separator, body = line.partition(' ')
        if not separator:
            continue
        try:
            state = json.loads(body)
        except ValueError:
            continue
        if isinstance(state, dict) and 'phase' in state:
            latest_phase = at, state
    try:
        at, state = latest_phase or ('', {})
        # Operator/node clocks were observed 61ms apart. Allow at most one
        # second of clock skew; the existing ten-second maximum age is fixed.
        fresh = -1 <= (now - timestamp(at)).total_seconds() <= 10
    except (TypeError, ValueError):
        fresh = False
    if (not fresh or state != {'phase': 'observing', 'reason': 'no-current-oom-at-low-limit'}):
        raise Blocked('memory controller is not freshly observing an idle baseline')
    return container['name']


def owned_pods(config, deployment, pods, replicasets, *, memory):
    validate_deployment(config, deployment, memory=memory)
    live = [p for p in pods if not p['metadata'].get('deletionTimestamp')]
    for pod in live:
        owner = next((o for o in pod['metadata'].get('ownerReferences', []) if o.get('controller')), {})
        rs = next((r for r in replicasets if r['metadata']['uid'] == owner.get('uid')), {})
        if (owner.get('kind') != 'ReplicaSet'
            or not any(o.get('controller') and o.get('kind') == 'Deployment'
                       and o.get('uid') == config.target['deployment_uid']
                       for o in rs.get('metadata', {}).get('ownerReferences', []))
            or len(pod['spec']['containers']) != 1
            or any(pod['spec']['containers'][0].get(k) != deployment['spec']['template']['spec']['containers'][0].get(k)
                   for k in ('name', 'image', 'resources'))):
            raise Blocked('pod owner, image or resources do not match the dedicated deployment')
    return live


def controller_preflight(config, kube):
    controller = kube.get('deployment', config.controller['deployment'])
    candidates = [c for c in controller['spec']['template']['spec']['containers']
                  if any(a == 'scripts.run_pod_memory_demo' or a.endswith('/run_pod_memory_demo.py')
                         for a in c.get('command', []) + c.get('args', []))]
    if len(candidates) != 1:
        raise Blocked('memory controller entrypoint missing')
    cm = kube.get('configmap', config.controller['configmap'])
    logs = kube.run(['logs', 'deployment/' + config.controller['deployment'], '-n', NAMESPACE,
                     '-c', candidates[0]['name'], '--tail=30', '--timestamps=true'], json_result=False)
    validate_controller(config, controller, cm, logs, now=clock())


def preflight(config, kube):
    for name, field in (('kube-system', 'cluster_uid'), (NAMESPACE, 'namespace_uid')):
        if kube.get('namespace', name, namespace=None)['metadata']['uid'] != config.target[field]:
            raise Blocked('cluster or namespace UID mismatch')
    deployment = kube.get('deployment', NAME)
    if not ready(deployment):
        raise Blocked('OOM workload must be Ready at 256Mi before a trigger')
    if any(h['spec']['scaleTargetRef'].get('kind') == 'Deployment'
           and h['spec']['scaleTargetRef'].get('name') == NAME for h in kube.get('hpa')['items']):
        raise Blocked('OOM workload has an HPA')
    pods = owned_pods(config, deployment, kube.get('pods', selector='app=' + NAME)['items'],
                      kube.get('replicasets', selector='app=' + NAME)['items'], memory='256Mi')
    if len(pods) != 1 or not pods[0]['spec'].get('nodeName'):
        raise Blocked('OOM workload requires exactly one scheduled baseline pod')
    pod_status = pods[0].get('status', {})
    conditions = {c['type']: c['status'] for c in pod_status.get('conditions', [])}
    containers = [c for c in pod_status.get('containerStatuses', []) if c.get('name') == CONTAINER]
    if (pod_status.get('phase') != 'Running' or conditions.get('Ready') != 'True'
        or len(containers) != 1 or containers[0].get('ready') is not True
        or not isinstance(containers[0].get('state', {}).get('running'), dict)):
        raise Blocked('the owned baseline pod and OOM container must both be Ready and running')
    node = kube.get('node', pods[0]['spec']['nodeName'], namespace=None)
    conditions = {c['type']: c['status'] for c in node['status'].get('conditions', [])}
    if conditions.get('Ready') != 'True' or conditions.get('MemoryPressure') != 'False':
        raise Blocked('node is not Ready or has memory pressure')
    # Read worker state last; lengthy workload checks must not turn a stale
    # idle observation into permission to trigger another episode.
    controller_preflight(config, kube)
    return deployment


def trigger_patch(config, deployment):
    validate_deployment(config, deployment)
    # Full spec test prevents concurrent HPA/template edits as well as ordinary
    # resourceVersion drift. Only this one memory limit is ever replaced.
    return [{'op': 'test', 'path': '/metadata/uid', 'value': config.target['deployment_uid']},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': deployment['metadata']['resourceVersion']},
            {'op': 'test', 'path': '/metadata/generation', 'value': deployment['metadata']['generation']},
            {'op': 'test', 'path': '/spec', 'value': deployment['spec']},
            {'op': 'replace', 'path': '/spec/template/spec/containers/0/resources/limits/memory', 'value': '64Mi'}]


def observe_oom(config, kube, generation, started, *, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        deployment = kube.get('deployment', NAME)
        if deployment['metadata']['generation'] != generation:
            raise Blocked('deployment changed after the trigger; outcome needs operator review')
        # During replacement, the old 256Mi pod may still be terminating. Only
        # examine live pods once they all match the new template.
        try:
            pods = owned_pods(config, deployment, kube.get('pods', selector='app=' + NAME)['items'],
                              kube.get('replicasets', selector='app=' + NAME)['items'], memory='64Mi')
        except ReadTimeout:
            raise  # Exhausted reads must not restart through the rollout-observation loop.
        except Blocked:
            time.sleep(2)
            continue
        for pod in pods:
            for status in pod.get('status', {}).get('containerStatuses', []):
                termination = status.get('state', {}).get('terminated') or status.get('lastState', {}).get('terminated') or {}
                if (status['name'] == CONTAINER and termination.get('reason') == 'OOMKilled'
                    and termination.get('exitCode') == 137
                    and started <= timestamp(termination['finishedAt']) <= clock()):
                    print(json.dumps({'phase': 'oom_observed', 'pod_uid': pod['metadata']['uid'],
                                      'reason': 'OOMKilled', 'exit_code': 137,
                                      'finished_at': termination['finishedAt'],
                                      'next': 'review the memory proposal and Approve in Operator UI'}), flush=True)
                    return True
        time.sleep(2)
    print('UNKNOWN: OOM evidence timed out. No retry or automatic recovery was attempted.', flush=True)
    return False


def run(config, *, check=False, kube=None):
    kube = kube or Kubectl(config)
    fd = os.open(config.lock_file, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'a') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise Blocked('OOM lock must be a regular file owned by this operator')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Blocked('an OOM trigger is already active') from exc
        try:
            deployment = preflight(config, kube)
        except ReadTimeout as exc:
            raise Blocked('Kubernetes read timed out after 2 attempts; no memory-change PATCH was sent') from exc
        if check:
            print('CHECK PASS: bound OOM target/controller at a healthy 256Mi baseline. No PATCH.', flush=True)
            return 0
        if os.environ.get('DEMO_CONSOLE_OOM_EXECUTE') != '1':
            raise Blocked('DEMO_CONSOLE_OOM_EXECUTE=1 is required; no PATCH sent')
        started = clock()
        print('CAS trigger: exact bound Deployment UID/resourceVersion/generation/spec; memory 256Mi -> 64Mi only.', flush=True)
        # /spec may include environment values or annotations. Pass the patch on
        # stdin rather than putting it in argv, terminal output or a temp file.
        result = kube.run(['patch', 'deployment', NAME, '-n', NAMESPACE, '--type=json',
                           '--patch-file=/dev/stdin', '-o', 'json'], input_json=trigger_patch(config, deployment))
        validate_deployment(config, result, memory='64Mi')
        if result['metadata']['generation'] != deployment['metadata']['generation'] + 1:
            raise Blocked('unexpected PATCH response generation; do not retry')
        print('Trigger acknowledged: memory 256Mi -> 64Mi. Waiting for actual OOMKilled evidence.', flush=True)
        return 0 if observe_oom(config, kube, result['metadata']['generation'], started) else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Read-only target/controller preflight')
    args = parser.parse_args()
    try:
        raise SystemExit(run(Config.from_env(), check=args.check))
    except (Blocked, OSError, KeyError, TypeError, ValueError) as exc:
        # No raw CLI output, environment or config contents in the terminal.
        print('BLOCKED/UNKNOWN: ' + (str(exc) if isinstance(exc, Blocked) else type(exc).__name__), flush=True)
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
