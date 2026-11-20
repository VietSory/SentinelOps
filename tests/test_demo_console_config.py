"""Offline configuration and OOM trigger boundaries; never run kubectl."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from scripts.demo_console.deployment.render import render
from scripts.demo_console import trigger_oom as oom


IMAGE = 'example.test/demo@sha256:' + 'a' * 64


def config(tmp_path):
    return oom.Config(
        {'cluster_uid': 'cluster-1', 'namespace_uid': 'ns-1', 'deployment_uid': 'dep-1',
         'image': IMAGE, 'namespace': oom.NAMESPACE, 'deployment': oom.NAME, 'container': oom.CONTAINER},
        {'deployment': 'memory-controller', 'uid': 'controller-1', 'configmap': 'memory-target', 'key': 'target.json'},
        '/operator/kubeconfig', 'sentinelops-demo', str(tmp_path / 'trigger.lock'))


def deployment():
    return {'metadata': {'name': oom.NAME, 'namespace': oom.NAMESPACE, 'uid': 'dep-1',
                         'resourceVersion': '7', 'generation': 3},
            'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': oom.NAME}},
                     'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 0, 'maxUnavailable': 1}},
                     'template': {'metadata': {'labels': dict(oom.LABELS)}, 'spec': {'containers': [
                         {'name': oom.CONTAINER, 'image': IMAGE, 'resources': deepcopy(oom.RESOURCES)}]}}},
            'status': {'observedGeneration': 3, 'replicas': 1, 'readyReplicas': 1, 'updatedReplicas': 1}}


def controller():
    value = deployment()
    value['metadata'].update(name='memory-controller', uid='controller-1')
    value['spec']['template']['spec'] = {
        'containers': [{'name': 'memory-controller', 'image': IMAGE,
                        'command': ['python', '-m', 'scripts.run_pod_memory_demo'],
                        'args': ['--target', '/target/target.json', '--policy-enabled'],
                        'volumeMounts': [{'name': 'target', 'mountPath': '/target', 'readOnly': True}]}],
        'volumes': [{'name': 'target', 'configMap': {'name': 'memory-target'}}]}
    return value


def pods(memory='256Mi'):
    container = deployment()['spec']['template']['spec']['containers'][0]
    container['resources']['limits']['memory'] = memory
    return [{'metadata': {'uid': 'pod-1', 'ownerReferences': [
                 {'kind': 'ReplicaSet', 'uid': 'rs-1', 'controller': True}]},
             'spec': {'nodeName': 'node-1', 'containers': [container]},
             'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                        'containerStatuses': [{'name': oom.CONTAINER, 'ready': True,
                                              'state': {'running': {'startedAt': '2026-11-20T12:00:00Z'}}}]}}]


def replica_sets():
    return [{'metadata': {'uid': 'rs-1', 'ownerReferences': [
        {'kind': 'Deployment', 'uid': 'dep-1', 'controller': True}]}}]


class FakeKube:
    def __init__(self, cfg):
        self.config, self.commands = cfg, []
        self.workload = deployment()
        self.controller = controller()
        self.node_conditions = [{'type': 'Ready', 'status': 'True'}, {'type': 'MemoryPressure', 'status': 'False'}]
        self.hpas = []
        self.observed = pods()

    def get(self, kind, name=None, **kwargs):
        self.commands.append(('get', kind, name))
        if kind == 'namespace':
            return {'metadata': {'uid': 'cluster-1' if name == 'kube-system' else 'ns-1'}}
        if kind == 'deployment':
            return deepcopy(self.workload if name == oom.NAME else self.controller)
        if kind == 'configmap':
            return {'data': {'target.json': json.dumps(self.config.target)}}
        if kind == 'hpa':
            return {'items': self.hpas}
        if kind == 'pods':
            return {'items': deepcopy(self.observed)}
        if kind == 'replicasets':
            return {'items': replica_sets()}
        if kind == 'node':
            return {'status': {'conditions': self.node_conditions}}
        raise AssertionError(kind)

    def run(self, args, *, json_result=True, input_json=None):
        self.commands.append(tuple(args))
        if args[0] == 'logs':
            return oom.clock().isoformat() + ' ' + json.dumps(
                {'phase': 'observing', 'reason': 'no-current-oom-at-low-limit'})
        assert args[:3] == ['patch', 'deployment', oom.NAME]
        assert '--patch-file=/dev/stdin' in args and '-p' not in args
        assert input_json == oom.trigger_patch(self.config, self.workload)
        self.workload['metadata']['generation'] += 1
        self.workload['spec']['template']['spec']['containers'][0]['resources']['limits']['memory'] = '64Mi'
        return deepcopy(self.workload)


def test_renderer_explicit_opt_in_never_adds_credentials_or_script_runners():
    for enabled in (False, True):
        manifest = render(IMAGE, enable_faults=enabled)
        assert [v['kind'] for v in manifest['items']] == ['ServiceAccount', 'Deployment', 'NetworkPolicy']
        pod = manifest['items'][1]['spec']['template']['spec']
        env = {v['name']: v['value'] for v in pod['containers'][0]['env']}
        assert env['DEMO_CONSOLE_ENABLE_FAULTS'] == str(enabled).lower()
        assert env['DEMO_CONSOLE_OOM_SCRIPT'] == env['DEMO_CONSOLE_DEPLOY_SCRIPT'] == ''
        assert env['DEMO_CONSOLE_ENABLE_OOM'] == 'false'
        assert pod['automountServiceAccountToken'] is False
        assert not pod.get('volumes')
        assert manifest['items'][2]['spec']['ingress'] == []
    assert render(IMAGE) == render(IMAGE, enable_faults=False)


def test_trigger_patch_tests_cas_and_entire_spec_before_only_fixed_memory_change(tmp_path):
    cfg, dep = config(tmp_path), deployment()
    patch = oom.trigger_patch(cfg, dep)
    assert {x['path']: x['value'] for x in patch if x['op'] == 'test'} == {
        '/metadata/uid': 'dep-1', '/metadata/resourceVersion': '7', '/metadata/generation': 3, '/spec': dep['spec']}
    assert [x for x in patch if x['op'] != 'test'] == [
        {'op': 'replace', 'path': '/spec/template/spec/containers/0/resources/limits/memory', 'value': '64Mi'}]
    assert dep['spec']['template']['spec']['containers'][0]['resources']['limits']['memory'] == '256Mi'


@pytest.mark.parametrize('change', ['uid', 'name', 'image', 'replicas', 'memory', 'label', 'init'])
def test_wrong_target_or_resource_drift_never_builds_a_patch(tmp_path, change):
    cfg, dep = config(tmp_path), deployment()
    container = dep['spec']['template']['spec']['containers'][0]
    if change == 'uid': dep['metadata']['uid'] = 'other'
    if change == 'name': dep['metadata']['name'] = 'payment-service'
    if change == 'image': container['image'] = 'example.test/other@sha256:' + 'b' * 64
    if change == 'replicas': dep['spec']['replicas'] = 2
    if change == 'memory': container['resources']['limits']['memory'] = '64Mi'
    if change == 'label': dep['spec']['template']['metadata']['labels']['sentinelops.io/demo'] = 'other'
    if change == 'init': dep['spec']['template']['spec']['initContainers'] = [{'name': 'other'}]
    with pytest.raises(oom.Blocked):
        oom.trigger_patch(cfg, dep)


def test_read_only_check_checks_live_dependencies_and_never_patches(tmp_path, capsys):
    cfg = config(tmp_path)
    kube = FakeKube(cfg)
    assert oom.run(cfg, check=True, kube=kube) == 0
    assert all(cmd[0] != 'patch' for cmd in kube.commands)
    assert 'No PATCH' in capsys.readouterr().out


def test_execute_gate_is_required_and_never_patches(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    kube = FakeKube(cfg)
    monkeypatch.delenv('DEMO_CONSOLE_OOM_EXECUTE', raising=False)
    with pytest.raises(oom.Blocked, match='EXECUTE=1'):
        oom.run(cfg, kube=kube)
    assert all(cmd[0] != 'patch' for cmd in kube.commands)


@pytest.mark.parametrize('guard', ['cluster', 'hpa', 'node', 'controller', 'pending', 'target-map', 'workload'])
def test_failed_preflight_never_mutates(tmp_path, monkeypatch, guard):
    cfg = config(tmp_path)
    kube = FakeKube(cfg)
    monkeypatch.setenv('DEMO_CONSOLE_OOM_EXECUTE', '1')
    if guard == 'cluster': cfg = replace(cfg, target={**cfg.target, 'cluster_uid': 'wrong'})
    if guard == 'hpa': kube.hpas = [{'spec': {'scaleTargetRef': {'kind': 'Deployment', 'name': oom.NAME}}}]
    if guard == 'node': kube.node_conditions[-1]['status'] = 'True'
    if guard == 'controller': kube.controller['status']['readyReplicas'] = 0
    if guard == 'pending':
        previous = kube.run
        kube.run = lambda args, **kwargs: (oom.clock().isoformat() + ' {"phase":"pending"}'
                                          if args[0] == 'logs' else previous(args, **kwargs))
    if guard == 'target-map': kube.controller['spec']['template']['spec']['containers'][0]['args'][1] = '/other/target.json'
    if guard == 'workload': kube.workload['metadata']['uid'] = 'other'
    with pytest.raises(oom.Blocked):
        oom.run(cfg, kube=kube)
    assert all(cmd[0] != 'patch' for cmd in kube.commands)


@pytest.mark.parametrize('state', ['pod-unready', 'container-unready', 'waiting', 'terminated', 'missing-status'])
def test_stale_deployment_ready_cannot_authorize_an_unready_baseline_pod(tmp_path, monkeypatch, state):
    cfg = config(tmp_path)
    kube = FakeKube(cfg)
    assert oom.ready(kube.workload)  # Deployment counters still describe the old healthy observation.
    pod_status = kube.observed[0]['status']
    container_status = pod_status['containerStatuses'][0]
    if state == 'pod-unready': pod_status['conditions'][0]['status'] = 'False'
    if state == 'container-unready': container_status['ready'] = False
    if state == 'waiting': container_status['state'] = {'waiting': {'reason': 'CrashLoopBackOff'}}
    if state == 'terminated': container_status['state'] = {'terminated': {'reason': 'Error', 'exitCode': 1}}
    if state == 'missing-status': pod_status['containerStatuses'] = []
    monkeypatch.setenv('DEMO_CONSOLE_OOM_EXECUTE', '1')
    with pytest.raises(oom.Blocked, match='Ready and running'):
        oom.run(cfg, kube=kube)
    assert all(command[0] != 'patch' for command in kube.commands)


def test_controller_stale_log_and_disabled_policy_are_blocked(tmp_path):
    cfg = config(tmp_path)
    cm = {'data': {'target.json': json.dumps(cfg.target)}}
    old = '2026-01-01T00:00:00Z {"phase":"observing","reason":"no-current-oom-at-low-limit"}'
    with pytest.raises(oom.Blocked, match='freshly'):
        oom.validate_controller(cfg, controller(), cm, old, now=datetime(2026, 11, 20, tzinfo=timezone.utc))
    dep = controller()
    dep['spec']['template']['spec']['containers'][0]['args'].remove('--policy-enabled')
    with pytest.raises(oom.Blocked, match='policy'):
        oom.validate_controller(cfg, dep, cm, old, now=oom.clock())


def test_controller_latest_phase_survives_interleaved_nonphase_otel_diagnostics(tmp_path):
    cfg=config(tmp_path)
    now=datetime(2026,11,21,tzinfo=timezone.utc)
    cm={'data':{cfg.controller['key']:json.dumps(cfg.target)}}
    healthy=(now-timedelta(seconds=2)).isoformat()+' {"phase":"observing","reason":"no-current-oom-at-low-limit"}'
    diagnostics=now.isoformat()+' ERROR OTLP metric exporter unavailable\n'+now.isoformat()+' {"severity":"error","component":"otlp"}'
    assert oom.validate_controller(cfg,controller(),cm,healthy+'\n'+diagnostics,now=now)=='memory-controller'


def test_actual_kubernetes_nanosecond_log_with_measured_61ms_clock_skew_is_fresh(tmp_path):
    cfg=config(tmp_path)
    now=datetime.fromisoformat('2026-11-20T19:10:19.088052+00:00')
    cm={'data':{cfg.controller['key']:json.dumps(cfg.target)}}
    log='2026-11-20T19:10:19.148966832Z {"phase":"observing","reason":"no-current-oom-at-low-limit"}'
    assert oom.validate_controller(cfg,controller(),cm,log,now=now)=='memory-controller'


@pytest.mark.parametrize('age,accepted',[(-1,True),(-1.000001,False),(10,True),(10.000001,False)])
def test_controller_log_keeps_one_second_future_and_ten_second_age_bounds(tmp_path,age,accepted):
    cfg=config(tmp_path)
    now=datetime(2026,11,21,tzinfo=timezone.utc)
    cm={'data':{cfg.controller['key']:json.dumps(cfg.target)}}
    log=(now-timedelta(seconds=age)).isoformat()+' {"phase":"observing","reason":"no-current-oom-at-low-limit"}'
    if accepted:
        assert oom.validate_controller(cfg,controller(),cm,log,now=now)=='memory-controller'
    else:
        with pytest.raises(oom.Blocked,match='freshly'):
            oom.validate_controller(cfg,controller(),cm,log,now=now)


def test_clock_skew_tolerance_never_accepts_a_newer_pending_phase(tmp_path):
    cfg=config(tmp_path)
    now=datetime(2026,11,21,tzinfo=timezone.utc)
    cm={'data':{cfg.controller['key']:json.dumps(cfg.target)}}
    old=(now-timedelta(seconds=1)).isoformat()+' {"phase":"observing","reason":"no-current-oom-at-low-limit"}'
    pending=(now+timedelta(milliseconds=61)).isoformat()+' {"phase":"pending","reason":"oncall-approval-required"}'
    with pytest.raises(oom.Blocked,match='freshly'):
        oom.validate_controller(cfg,controller(),cm,old+'\n'+pending,now=now)


@pytest.mark.parametrize('phase',['pending','unavailable','blocked','verifying'])
def test_latest_controller_phase_blocks_despite_an_older_healthy_record(tmp_path,phase):
    cfg=config(tmp_path)
    now=datetime(2026,11,21,tzinfo=timezone.utc)
    cm={'data':{cfg.controller['key']:json.dumps(cfg.target)}}
    healthy=(now-timedelta(seconds=2)).isoformat()+' {"phase":"observing","reason":"no-current-oom-at-low-limit"}'
    latest=now.isoformat()+' '+json.dumps({'phase':phase,'reason':'not-idle'})
    diagnostics=now.isoformat()+' exporter diagnostic without controller phase'
    with pytest.raises(oom.Blocked,match='freshly'):
        oom.validate_controller(cfg,controller(),cm,healthy+'\n'+latest+'\n'+diagnostics,now=now)


def test_only_stale_controller_phase_with_fresh_sdk_diagnostics_remains_blocked(tmp_path):
    cfg=config(tmp_path)
    now=datetime(2026,11,21,tzinfo=timezone.utc)
    cm={'data':{cfg.controller['key']:json.dumps(cfg.target)}}
    old=(now-timedelta(seconds=11)).isoformat()+' {"phase":"observing","reason":"no-current-oom-at-low-limit"}'
    fresh=now.isoformat()+' OTLP exporter retrying'
    with pytest.raises(oom.Blocked,match='freshly'):
        oom.validate_controller(cfg,controller(),cm,old+'\n'+fresh,now=now)


def test_real_oom_reason_and_new_termination_clock_required(tmp_path, monkeypatch, capsys):
    cfg = config(tmp_path)
    kube = FakeKube(cfg)
    started = oom.clock()
    kube.workload['spec']['template']['spec']['containers'][0]['resources']['limits']['memory'] = '64Mi'
    kube.observed = pods('64Mi')
    status = {'name': oom.CONTAINER, 'state': {'terminated': {
        'reason': 'Error', 'exitCode': 137, 'finishedAt': started.isoformat()}}}
    kube.observed[0]['status']['containerStatuses'] = [status]
    ticks = iter([0, 0, 1])
    monkeypatch.setattr(oom.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(oom.time, 'sleep', lambda _: None)
    assert not oom.observe_oom(cfg, kube, 3, started, timeout=1)
    assert 'UNKNOWN' in capsys.readouterr().out
    status['state']['terminated']['reason'] = 'OOMKilled'
    ticks = iter([0, 0])
    monkeypatch.setattr(oom.time, 'monotonic', lambda: next(ticks))
    assert oom.observe_oom(cfg, kube, 3, started, timeout=1)
    text = capsys.readouterr().out
    assert 'oom_observed' in text and 'Approve' in text


def test_one_guarded_patch_then_observation_without_auto_recovery(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    kube = FakeKube(cfg)
    monkeypatch.setenv('DEMO_CONSOLE_OOM_EXECUTE', '1')
    monkeypatch.setattr(oom, 'observe_oom', lambda *args: True)
    assert oom.run(cfg, kube=kube) == 0
    assert len([cmd for cmd in kube.commands if cmd[0] == 'patch']) == 1
    assert kube.workload['spec']['template']['spec']['containers'][0]['resources']['limits']['memory'] == '64Mi'


def test_real_kubectl_adapter_requests_json_patch_response_and_observes_once(tmp_path, monkeypatch):
    """kubectl defaults to human-readable 'patched'; exercise the actual decoder."""
    cfg, dep = config(tmp_path), deployment()
    response = deepcopy(dep)
    response['metadata']['generation'] += 1
    response['spec']['template']['spec']['containers'][0]['resources']['limits']['memory'] = '64Mi'
    calls, observations = [], []
    def subprocess_run(command, **kwargs):
        calls.append((command, kwargs))
        assert command[:len(kube.base)] == kube.base
        assert command[len(kube.base):len(kube.base) + 3] == ['patch', 'deployment', oom.NAME]
        assert json.loads(kwargs['input']) == oom.trigger_patch(cfg, dep)
        # Model the provider's actual output contract, including its non-JSON default.
        stdout = json.dumps(response) if command[-2:] == ['-o', 'json'] else 'deployment.apps/' + oom.NAME + ' patched\n'
        return oom.subprocess.CompletedProcess(command, 0, stdout, '')
    def observe(configured, client, generation, started):
        observations.append((configured, client, generation))
        return True
    kube = oom.Kubectl(cfg)
    monkeypatch.setenv('DEMO_CONSOLE_OOM_EXECUTE', '1')
    monkeypatch.setattr(oom, 'preflight', lambda configured, client: deepcopy(dep))
    monkeypatch.setattr(oom, 'observe_oom', observe)
    monkeypatch.setattr(oom.subprocess, 'run', subprocess_run)
    assert oom.run(cfg, kube=kube) == 0
    assert len(calls) == len(observations) == 1
    assert observations[0] == (cfg, kube, 4)
    assert calls[0][0][-2:] == ['-o', 'json']


def test_no_raw_kubectl_stderr_or_credentials_printed(tmp_path, monkeypatch, capsys):
    def failed(*args, **kwargs):
        class Result:
            returncode = 1
            stdout = ''
            stderr = 'Authorization: Bearer private-credential'
        return Result()
    monkeypatch.setattr(oom.subprocess, 'run', failed)
    with pytest.raises(oom.Blocked):
        oom.Kubectl(config(tmp_path)).run(['get', 'namespace', 'kube-system', '-o', 'json'])
    assert 'private-credential' not in capsys.readouterr().out


@pytest.mark.parametrize('operation', ['get', 'logs'])
@pytest.mark.parametrize('failure', ['stderr', 'expired'])
def test_read_timeout_retries_once_then_returns_the_healthy_response(tmp_path, monkeypatch, capsys,
                                                                   operation, failure):
    calls, sleeps = [], []
    def subprocess_run(command, **kwargs):
        calls.append((command, kwargs))
        if len(calls) == 1:
            if failure == 'expired':
                raise oom.subprocess.TimeoutExpired(command, kwargs['timeout'], stderr='private-credential')
            return oom.subprocess.CompletedProcess(command, 1, '',
                'Unable to connect to the server: i/o timeout; private-credential')
        return oom.subprocess.CompletedProcess(command, 0, '{"healthy":true}', '')
    monkeypatch.setattr(oom.subprocess, 'run', subprocess_run)
    monkeypatch.setattr(oom.time, 'sleep', sleeps.append)
    kube = oom.Kubectl(config(tmp_path))
    if operation == 'get':
        assert kube.get('namespace', 'kube-system', namespace=None) == {'healthy': True}
    else:
        assert kube.run(['logs', 'deployment/memory-controller'], json_result=False) == '{"healthy":true}'
    assert len(calls) == 2 and calls[0][0] == calls[1][0]
    assert all('--request-timeout=10s' in command and kwargs['timeout'] == 15 for command, kwargs in calls)
    assert sleeps == [.25] and sum(kwargs['timeout'] for _, kwargs in calls) + sum(sleeps) <= 31
    output = capsys.readouterr().out
    assert 'retrying once (2/2)' in output and 'private-credential' not in output


@pytest.mark.parametrize('failure', ['stderr', 'expired'])
def test_read_timeouts_exhaust_exactly_two_attempts(tmp_path, monkeypatch, capsys, failure):
    calls, sleeps = [], []
    def subprocess_run(command, **kwargs):
        calls.append(command)
        if failure == 'expired':
            raise oom.subprocess.TimeoutExpired(command, kwargs['timeout'], stderr='private-credential')
        return oom.subprocess.CompletedProcess(command, 1, '',
            'net/http: TLS handshake timeout; private-credential')
    monkeypatch.setattr(oom.subprocess, 'run', subprocess_run)
    monkeypatch.setattr(oom.time, 'sleep', sleeps.append)
    with pytest.raises(oom.ReadTimeout, match='read timed out after 2 attempts'):
        oom.Kubectl(config(tmp_path)).get('namespace', 'kube-system', namespace=None)
    assert len(calls) == 2 and sleeps == [.25]
    assert 'private-credential' not in capsys.readouterr().out


@pytest.mark.parametrize('failure', ['stderr', 'expired'])
def test_patch_timeout_is_never_retried_or_observed_as_acknowledged(tmp_path, monkeypatch, capsys, failure):
    cfg, calls, sleeps = config(tmp_path), [], []
    def subprocess_run(command, **kwargs):
        calls.append(command)
        assert command[len(kube.base)] == 'patch'
        if failure == 'expired':
            raise oom.subprocess.TimeoutExpired(command, kwargs['timeout'], stderr='private-credential')
        return oom.subprocess.CompletedProcess(command, 1, '', 'i/o timeout; private-credential')
    kube = oom.Kubectl(cfg)
    monkeypatch.setenv('DEMO_CONSOLE_OOM_EXECUTE', '1')
    monkeypatch.setattr(oom, 'preflight', lambda *args: deployment())
    monkeypatch.setattr(oom.subprocess, 'run', subprocess_run)
    monkeypatch.setattr(oom.time, 'sleep', sleeps.append)
    monkeypatch.setattr(oom, 'observe_oom', lambda *args: pytest.fail('uncertain PATCH must not be acknowledged'))
    with pytest.raises(oom.Blocked) as exc:
        oom.run(cfg, kube=kube)
    assert len(calls) == 1 and sleeps == []
    assert 'no memory-change PATCH' not in str(exc.value)
    output = capsys.readouterr().out
    assert 'Trigger acknowledged' not in output and 'private-credential' not in output


def test_non_timeout_read_error_is_not_retried(tmp_path, monkeypatch):
    calls = []
    def forbidden(command, **kwargs):
        calls.append(command)
        return oom.subprocess.CompletedProcess(command, 1, '', 'Forbidden: denied by RBAC')
    monkeypatch.setattr(oom.subprocess, 'run', forbidden)
    monkeypatch.setattr(oom.time, 'sleep', lambda _: pytest.fail('non-timeout error must not back off'))
    with pytest.raises(oom.Blocked, match='request failed'):
        oom.Kubectl(config(tmp_path)).get('namespace', 'kube-system', namespace=None)
    assert len(calls) == 1


@pytest.mark.parametrize('phase', ['preflight', 'deployment', 'pods', 'replicasets'])
def test_read_timeout_message_only_claims_no_patch_before_trigger(tmp_path, monkeypatch, phase):
    cfg, calls = config(tmp_path), []
    kube = oom.Kubectl(cfg)
    triggered = deployment()
    triggered['metadata']['generation'] += 1
    triggered['spec']['template']['spec']['containers'][0]['resources']['limits']['memory'] = '64Mi'
    def subprocess_run(command, **kwargs):
        operation = command[len(kube.base)]
        kind = command[len(kube.base) + 1]
        calls.append((operation, kind))
        if operation == 'patch':
            return oom.subprocess.CompletedProcess(command, 0, json.dumps(triggered), '')
        if kind == 'deployment' and phase in ('pods', 'replicasets'):
            return oom.subprocess.CompletedProcess(command, 0, json.dumps(triggered), '')
        if kind == 'pods' and phase == 'replicasets':
            return oom.subprocess.CompletedProcess(command, 0, json.dumps({'items': pods('64Mi')}), '')
        raise oom.subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setenv('DEMO_CONSOLE_OOM_EXECUTE', '1')
    monkeypatch.setattr(oom.subprocess, 'run', subprocess_run)
    monkeypatch.setattr(oom.time, 'sleep', lambda _: None)
    if phase != 'preflight':
        monkeypatch.setattr(oom, 'preflight', lambda *args: deployment())
    with pytest.raises(oom.Blocked) as exc:
        oom.run(cfg, kube=kube)
    assert ('no memory-change PATCH was sent' in str(exc.value)) == (phase == 'preflight')
    failed = 'namespace' if phase == 'preflight' else phase
    expected = [] if phase == 'preflight' else [('patch', 'deployment')]
    if phase in ('pods', 'replicasets'):
        expected += [('get', 'deployment')]
    if phase == 'replicasets':
        expected += [('get', 'pods')]
    assert calls == expected + [('get', failed)] * 2


def test_controller_configmap_latency_does_not_age_the_latest_log(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    kube, seconds = FakeKube(cfg), [0]
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(oom, 'clock', lambda: now + timedelta(seconds=seconds[0]))
    get = kube.get
    def slow_get(kind, *args, **kwargs):
        result = get(kind, *args, **kwargs)
        if kind == 'configmap':
            seconds[0] += 11
        return result
    monkeypatch.setattr(kube, 'get', slow_get)
    assert oom.run(cfg, check=True, kube=kube) == 0
    assert seconds[0] == 11
    assert kube.commands[-2] == ('get', 'configmap', cfg.controller['configmap'])
    assert kube.commands[-1][0] == 'logs'


def test_full_spec_patch_goes_to_stdin_never_argv_or_terminal(tmp_path, monkeypatch, capsys):
    captured = []
    def success(argv, **kwargs):
        captured.append((argv, kwargs))
        class Result:
            returncode = 0
            stdout = '{}'
        return Result()
    monkeypatch.setattr(oom.subprocess, 'run', success)
    cfg, dep = config(tmp_path), deployment()
    dep['spec']['template']['spec']['containers'][0]['env'] = [
        {'name': 'PRIVATE_DEMO_VALUE', 'value': 'synthetic-sensitive-fixture'}]
    patch = oom.trigger_patch(cfg, dep)
    oom.Kubectl(cfg).run(['patch', 'deployment', oom.NAME, '-n', oom.NAMESPACE,
                         '--type=json', '--patch-file=/dev/stdin'], input_json=patch)
    argv, kwargs = captured[0]
    assert json.loads(kwargs['input']) == patch
    assert 'synthetic-sensitive-fixture' not in ' '.join(argv)
    assert 'synthetic-sensitive-fixture' not in capsys.readouterr().out


def test_symlink_lock_is_rejected_without_any_kubernetes_read_or_write(tmp_path):
    cfg = config(tmp_path)
    victim = tmp_path / 'untouched'
    victim.write_text('original')
    from pathlib import Path
    Path(cfg.lock_file).symlink_to(victim)
    kube = FakeKube(cfg)
    with pytest.raises(OSError):
        oom.run(cfg, check=True, kube=kube)
    assert kube.commands == [] and victim.read_text() == 'original'
