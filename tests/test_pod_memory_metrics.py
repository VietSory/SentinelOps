import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from opentelemetry.metrics import get_meter_provider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
import pytest

from scripts import run_pod_memory_demo as cli
from sentinelops.remediation.pod_memory import MemoryTarget
from sentinelops.remediation import pod_memory_metrics as module
from sentinelops.remediation.pod_memory_metrics import PodMemoryMetrics, PREFIX, PHASES
from sentinelops.telemetry.core import timestamp

NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)
TARGET = MemoryTarget('cluster', 'namespace', 'deployment', 'fixture/demo@sha256:' + 'a'*64)


def observation(**changes):
    return {'target': asdict(TARGET), 'observed_at': timestamp(NOW), 'memory': '64Mi',
            'ready': False, 'restart_count': 3, 'pod_uid': 'private-pod-uid', 'oom': True,
            'oom_finished_at': timestamp(NOW - timedelta(seconds=2)), **changes}


def collect(reader):
    data = reader.get_metrics_data()
    return {metric.name: list(metric.data.data_points) for resource in data.resource_metrics
            for scope in resource.scope_metrics for metric in scope.metrics}


@pytest.fixture
def metrics():
    reader, seconds = InMemoryMetricReader(), [NOW.timestamp()]
    value = PodMemoryMetrics(reader, target=TARGET, clock=lambda: seconds[0])
    try:
        yield value, reader, seconds
    finally:
        value.close()


def test_unobserved_stale_failed_and_future_data_never_appear_healthy(metrics):
    value, reader, seconds = metrics
    data = collect(reader)
    assert data[PREFIX+'observation_available'][0].value == 0
    assert PREFIX+'pod_ready' not in data and PREFIX+'memory_limit' not in data
    assert PREFIX+'last_observed_unixtime' not in data
    value.observe(observation(memory='256Mi', ready=True, oom=False, restart_count=0))
    data = collect(reader)
    assert data[PREFIX+'memory_limit'][0].value == 256*1024*1024
    assert data[PREFIX+'pod_ready'][0].value == 1
    assert data[PREFIX+'observation_available'][0].value == 1
    seconds[0] += 20
    assert collect(reader)[PREFIX+'observation_available'][0].value == 1
    seconds[0] += .001
    data = collect(reader)
    assert data[PREFIX+'observation_available'][0].value == 0
    assert PREFIX+'pod_ready' not in data and PREFIX+'restarts' not in data
    assert data[PREFIX+'last_observed_unixtime'][0].value == NOW.timestamp()
    seconds[0] = NOW.timestamp()-1
    assert collect(reader)[PREFIX+'observation_available'][0].value == 0
    seconds[0] = NOW.timestamp()
    value.unavailable()
    assert PREFIX+'pod_ready' not in collect(reader)


def test_verified_observed_events_deduplicate_and_restart_is_current_pod_gauge(metrics):
    value, reader, _ = metrics
    value.observe(observation())
    value.observe(observation())
    data = collect(reader)
    assert data[PREFIX+'oom_observed_events'][0].value == 1
    assert data[PREFIX+'restarts'][0].value == 3
    value.observe(observation(restart_count=4, oom_finished_at=timestamp(NOW-timedelta(seconds=1))))
    assert collect(reader)[PREFIX+'oom_observed_events'][0].value == 2
    value.observe(observation(pod_uid='replacement-private-uid',memory='256Mi',ready=True,
                              restart_count=0,oom=False,oom_finished_at=None))
    data = collect(reader)
    assert data[PREFIX+'restarts'][0].value == 0
    assert data[PREFIX+'oom_observed_events'][0].value == 2
    for name,points in data.items():
        for point in points:
            attributes=dict(point.attributes)
            if name in (PREFIX+'phase',PREFIX+'last_terminal_phase'):
                assert attributes.pop('phase') in PHASES
            assert attributes == {'namespace':TARGET.namespace,'workload':TARGET.deployment,
                                  'container':TARGET.container}


def test_event_dedup_state_is_bounded(metrics):
    value, reader, _ = metrics
    for number in range(module.SEEN_EVENTS+20):
        value.observe(observation(restart_count=number,
            oom_finished_at=timestamp(NOW-timedelta(seconds=module.SEEN_EVENTS+20-number))))
    assert len(value._seen) == module.SEEN_EVENTS
    last = collect(reader)[PREFIX+'oom_observed_events'][0].value
    value.observe(observation(restart_count=module.SEEN_EVENTS+19,
                              oom_finished_at=timestamp(NOW-timedelta(seconds=1))))
    assert collect(reader)[PREFIX+'oom_observed_events'][0].value == last


def test_same_oom_termination_is_one_event_when_restart_count_advances(metrics):
    value,reader,_=metrics
    value.observe(observation(restart_count=0))
    value.observe(observation(restart_count=1))
    assert collect(reader)[PREFIX+'oom_observed_events'][0].value==1
    assert collect(reader)[PREFIX+'restarts'][0].value==1
    value.observe(observation(restart_count=2,oom_finished_at=timestamp(NOW-timedelta(seconds=1))))
    assert collect(reader)[PREFIX+'oom_observed_events'][0].value==2


def test_old_last_state_is_not_counted_as_a_new_fresh_oom(metrics):
    value,reader,_=metrics
    value.observe(observation(oom_finished_at=timestamp(NOW-timedelta(seconds=181))))
    assert collect(reader)[PREFIX+'oom_observed_events'][0].value==0
    assert collect(reader)[PREFIX+'observation_available'][0].value==1
    value.observe(observation(oom_finished_at=timestamp(NOW-timedelta(seconds=180))))
    assert collect(reader)[PREFIX+'oom_observed_events'][0].value==1


def test_sdk_counter_is_called_outside_the_callback_state_lock(metrics,monkeypatch):
    value,reader,_=metrics
    original=value._events.add
    def add(amount,attributes):
        acquired=value._lock.acquire(blocking=False)
        assert acquired,'SDK call must not hold the callback state lock'
        value._lock.release()
        collect(reader)  # Collection may safely reenter observation callbacks.
        return original(amount,attributes)
    monkeypatch.setattr(value._events,'add',add)
    value.observe(observation())
    assert collect(reader)[PREFIX+'oom_observed_events'][0].value==1


@pytest.mark.parametrize('changes', [{'target':{**asdict(TARGET),'deployment_uid':'other'}},
    {'memory':'1Gi'},{'ready':1},{'restart_count':-1},{'restart_count':True},{'oom':1},
    {'oom_finished_at':timestamp(NOW+timedelta(seconds=1))}])
def test_foreign_or_invalid_observation_cannot_emit_resource_values(metrics,changes):
    value,reader,_=metrics
    with pytest.raises((ValueError,KeyError)):
        value.observe(observation(**changes))
    assert collect(reader)[PREFIX+'oom_observed_events'][0].value == 0
    assert PREFIX+'memory_limit' not in collect(reader)


def test_phase_taxonomy_and_historical_terminal_are_explicit_and_bounded(metrics):
    value,reader,seconds=metrics
    cold=collect(reader)
    assert next(p for p in cold[PREFIX+'phase'] if p.attributes['phase']=='unavailable').value==1
    assert sum(p.value for p in cold[PREFIX+'phase'])==1
    assert cold[PREFIX+'observation_available'][0].value==0
    value.phase('pending')
    points=collect(reader)[PREFIX+'phase']
    assert len(points)==len(PHASES) and sum(p.value for p in points)==1
    assert next(p for p in points if p.attributes['phase']=='pending').value==1
    value.phase('recovered')
    value.phase('observing')
    data=collect(reader)
    assert next(p for p in data[PREFIX+'last_terminal_phase'] if p.attributes['phase']=='recovered').value==1
    assert data[PREFIX+'last_terminal_unixtime'][0].value==NOW.timestamp()
    seconds[0]+=21
    phases=collect(reader)[PREFIX+'phase']
    assert next(p for p in phases if p.attributes['phase']=='unavailable').value==1
    assert all(p.value==0 for p in phases if p.attributes['phase']!='unavailable')
    assert PREFIX+'last_terminal_phase' in collect(reader)
    with pytest.raises(ValueError):value.phase('secret-free-form-reason')


def test_fresh_kubernetes_cannot_keep_a_stalled_controller_phase_active(metrics):
    value,reader,seconds=metrics
    value.phase('pending')
    value.observe(observation())
    assert next(p for p in collect(reader)[PREFIX+'phase'] if p.attributes['phase']=='pending').value==1
    seconds[0]+=21
    value.observe(observation(observed_at=timestamp(NOW+timedelta(seconds=21)),
        memory='256Mi',ready=True,oom=False,oom_finished_at=None,restart_count=0))
    current=collect(reader)
    assert current[PREFIX+'observation_available'][0].value==1
    assert current[PREFIX+'pod_ready'][0].value==1
    phases={p.attributes['phase']:p.value for p in current[PREFIX+'phase']}
    assert phases['unavailable']==1 and phases['pending']==0
    assert set(phases)==set(PHASES) and sum(phases.values())==1


@pytest.mark.parametrize('endpoint', ['http://localhost:4317','http://127.0.0.1:12345/',
    'http://otel-collector.sentinelops-b-actions.svc.cluster.local:4317'])
def test_explicit_private_otlp_endpoints(endpoint):
    assert module.metrics_endpoint(endpoint)==endpoint.rstrip('/')


@pytest.mark.parametrize('endpoint', ['https://localhost:4317','http://evil.invalid:4317',
    'http://user:secret@localhost:4317','http://localhost:4317/?token=secret',
    'http://localhost:4317/#token','http://otel-collector.sentinelops-b-actions.svc.cluster.local:8889',
    'http://127.0.0.2:4317','http://localhost:4317\n','http://localhost:99999'])
def test_metrics_never_accept_arbitrary_or_credential_bearing_export_targets(endpoint):
    with pytest.raises(ValueError):module.metrics_endpoint(endpoint)


def test_exporter_has_no_ambient_headers_proxy_or_resource_attributes(monkeypatch):
    reader,captured=InMemoryMetricReader(),{}
    before=get_meter_provider()
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_HEADERS','authorization=private-fixture-token')
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_METRICS_HEADERS','api-key=private-fixture-key')
    monkeypatch.setenv('OTEL_RESOURCE_ATTRIBUTES','service.name=foreign,private.path=secret')
    monkeypatch.setenv('HTTPS_PROXY','http://private-fixture:secret@invalid:80')
    def exporter(**kwargs):captured.update(kwargs);return object()
    monkeypatch.setattr(module,'OTLPMetricExporter',exporter)
    monkeypatch.setattr(module,'PeriodicExportingMetricReader',lambda *args,**kwargs:reader)
    value=PodMemoryMetrics.otlp('http://localhost:4317',target=TARGET)
    try:
        assert captured['headers']=={'x-sentinelops-component':'oom-demo-controller'}
        assert captured['channel_options']==(('grpc.enable_http_proxy',0),)
        assert captured['timeout']==1 and captured['insecure'] is True
        assert dict(value.provider._sdk_config.resource.attributes)=={
            'service.name':'sentinelops-oom-demo-controller','service.namespace':'sentinelops-control'}
        assert get_meter_provider() is before
    finally:value.close()


def test_pending_sampler_refreshes_real_reads_without_an_extra_patch(metrics):
    value,reader,_=metrics
    async def scenario():
        stop=asyncio.Event()
        class Kube:
            patch=AsyncMock()
            count=0
            async def observe(self,now):
                self.count+=1
                if self.count==2:stop.set()
                return observation(restart_count=self.count,oom=False,oom_finished_at=None)
        kube=Kube()
        value.phase('pending')
        await cli.sample_metrics(kube,value,stop,interval=.001,timeout=.1,now=lambda:NOW)
        assert kube.count==2
        kube.patch.assert_not_awaited()
    asyncio.run(scenario())
    assert collect(reader)[PREFIX+'restarts'][0].value==2
    assert collect(reader)[PREFIX+'observation_available'][0].value==1


@pytest.mark.parametrize('failure',['exception','timeout'])
def test_sampler_failure_and_timeout_are_isolated(metrics,failure):
    value,reader,_=metrics
    value.observe(observation())
    async def scenario():
        stop=asyncio.Event()
        class Kube:
            patch=AsyncMock()
            async def observe(self,now):
                stop.set()
                if failure=='timeout':await asyncio.sleep(10)
                raise RuntimeError('private-dsn-that-must-not-be-logged')
        kube=Kube()
        await cli.sample_metrics(kube,value,stop,interval=.001,timeout=.001)
        kube.patch.assert_not_awaited()
    asyncio.run(scenario())
    assert collect(reader)[PREFIX+'observation_available'][0].value==0
    assert PREFIX+'pod_ready' not in collect(reader)


@pytest.mark.parametrize('failure',['factory','phase','close','disabled'])
def test_metrics_failure_never_masks_real_pending_incident_write(tmp_path,monkeypatch,capsys,failure):
    from tests.test_pod_memory import fixture
    _,target,_,kube,repo,_,controller,_=fixture(tmp_path/'store')
    repo.check_ready=AsyncMock()
    kube.close=AsyncMock()
    pool=SimpleNamespace(close=AsyncMock())
    monkeypatch.setenv('SENTINELOPS_DATABASE_URL','postgresql://fixture@127.0.0.1/sentinelops_control')
    monkeypatch.setattr(cli.asyncpg,'create_pool',AsyncMock(return_value=pool))
    monkeypatch.setattr(cli,'KubernetesMemory',lambda *args,**kwargs:kube)
    monkeypatch.setattr(cli,'IncidentRepository',lambda *args:repo)
    metrics=SimpleNamespace(observe=Mock(),unavailable=Mock(),phase=Mock(),close=Mock())
    if failure in ('phase','close'):
        getattr(metrics,failure).side_effect=RuntimeError('private-metrics-failure-secret')
    factory=Mock(return_value=metrics)
    if failure=='factory':factory.side_effect=RuntimeError('private-metrics-failure-secret')
    monkeypatch.setattr(module.PodMemoryMetrics,'otlp',factory)
    path=tmp_path/'target.json';path.write_text(__import__('json').dumps(asdict(target)))
    args=SimpleNamespace(target=path,store=tmp_path/'store',server='https://cluster.invalid',
        token_file=tmp_path/'token',ca_file=tmp_path/'ca',kill_switch=tmp_path/'kill',policy_enabled=True,
        metrics_otlp_endpoint='http://localhost:4317')
    if failure=='disabled':
        del args.metrics_otlp_endpoint  # Older programmatic callers need no new field.
    async def scenario():
        callbacks=[]
        monkeypatch.setattr(asyncio.get_running_loop(),'add_signal_handler',lambda sig,callback:callbacks.append(callback))
        async def tick():
            result=await controller.tick()
            callbacks[0]()
            return result
        monkeypatch.setattr(cli,'MemoryController',lambda **kwargs:SimpleNamespace(tick=tick))
        await cli.run(args)
    asyncio.run(scenario())
    assert len(repo.history)==1 and repo.history[0]['context']['lifecycle_stage']=='OPEN'
    assert kube.patches==[]
    assert 'private-metrics-failure-secret' not in capsys.readouterr().out
    if failure=='disabled':factory.assert_not_called()
    kube.close.assert_awaited_once();pool.close.assert_awaited_once()
