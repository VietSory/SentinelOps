from copy import deepcopy
import json
import pytest
from scripts import lane_b


def rendered_fixture():
    return {'name':lane_b.NAME,'services':{name:{'ports':[{'host_ip':'127.0.0.1','published':str(public),
        'target':target,'protocol':'tcp'} for public,target in ports.items()]} for name,ports in lane_b.PORTS.items()}}


def test_b_ports_replace_all_a_ports_and_cannot_escape_loopback():
    expected = rendered_fixture()
    assert lane_b.validate_config(expected) == expected
    for change in ('project','public','port','service'):
        value = deepcopy(expected)
        if change == 'project': value['name'] = 'sentinelops-corpus-train-01'
        if change == 'public': value['services']['postgres']['ports'][0]['host_ip'] = '0.0.0.0'
        if change == 'port': value['services']['postgres']['ports'].append({'host_ip':'127.0.0.1','published':'5432','target':5432})
        if change == 'service': value['services']['foreign'] = {}
        with pytest.raises(ValueError): lane_b.validate_config(value)


def test_launcher_uses_b_roots_and_existing_worker_flags():
    health = lane_b.launch_arguments('health')
    incidents = lane_b.launch_arguments('incidents','stream-id')
    assert 'http://127.0.0.1:19090' in health and 'http://127.0.0.1:14317' in health
    assert str(lane_b.ROOT/'health') in incidents and 'sentinelops-b' in incidents
    assert 'http://127.0.0.1:26686' in incidents
    assert '--model-store' not in incidents
    with pytest.raises(ValueError): lane_b.launch_arguments('incidents')


def test_runtime_secret_is_private_idempotent_and_not_returned(tmp_path,monkeypatch):
    monkeypatch.setattr(lane_b,'ROOT',tmp_path/'b')
    identity = lane_b.init_runtime()
    token = (lane_b.ROOT/'api-token').read_bytes()
    assert len(token) >= 32 and 'api-token' not in json.dumps(identity)
    assert lane_b.init_runtime() == identity and (lane_b.ROOT/'api-token').read_bytes() == token
    assert (lane_b.ROOT/'api-token').stat().st_mode & 0o077 == 0
    monkeypatch.setenv('CONTROL_DATABASE_URL','postgresql://control_writer:SECRET@127.0.0.1:55435/sentinelops_control')
    with pytest.raises(ValueError,match='15433'): lane_b.runtime_environment()


def test_unknown_runtime_directory_is_never_appropriated(tmp_path,monkeypatch):
    monkeypatch.setattr(lane_b,'ROOT',tmp_path)
    (tmp_path/'foreign').write_text('preserve')
    with pytest.raises(ValueError,match='nonempty unregistered'): lane_b.init_runtime()
    assert not (tmp_path/'api-token').exists()


def test_runtime_cannot_inherit_lane_a_model_or_compose_settings(tmp_path,monkeypatch):
    monkeypatch.setattr(lane_b,'ROOT',tmp_path/'b')
    monkeypatch.delenv('CONTROL_DATABASE_URL',raising=False)
    monkeypatch.setenv('SENTINELOPS_MODEL_STORE','/foreign/model-store')
    monkeypatch.setenv('SENTINELOPS_MODEL_RELEASE_REF','artifact://foreign-release')
    monkeypatch.setenv('COMPOSE_PROJECT_NAME','sentinelops-corpus-train-01')
    environment = lane_b.runtime_environment()
    assert not any(key in environment for key in ('SENTINELOPS_MODEL_STORE','SENTINELOPS_MODEL_RELEASE_REF','COMPOSE_PROJECT_NAME'))
    assert ':15433/' in environment['CONTROL_DATABASE_URL'] and environment['SENTINELOPS_ENABLE_DEMO'] == 'false'


@pytest.mark.parametrize('owner',['/foreign/checkout','',None])
def test_resource_mutation_rejects_another_checkouts_project(monkeypatch,owner):
    def read(command,**kwargs):
        if command[:2] == ['docker','ps']: return 'fixture-container\n'
        return json.dumps({'com.docker.compose.project':'sentinelops-b','com.docker.compose.service':'postgres',
                           'com.docker.compose.project.working_dir':owner})
    monkeypatch.setattr(lane_b.subprocess,'check_output',read)
    with pytest.raises(ValueError,match='another checkout'): lane_b.require_owned_containers()


def test_h1_activation_is_explicit_paired_owned_and_reuses_existing_loader(tmp_path,monkeypatch):
    monkeypatch.setattr(lane_b,'ROOT',tmp_path/'b')
    monkeypatch.delenv('CONTROL_DATABASE_URL',raising=False)
    model = lane_b.ROOT/'models'/'handoff-v1'
    release = 'artifact://manifests/'+'a'*64+'.json'
    with pytest.raises(ValueError,match='both explicitly'): lane_b.runtime_environment(model)
    with pytest.raises(ValueError,match='materialized'): lane_b.runtime_environment('/foreign/models',release)
    environment = lane_b.runtime_environment(model,release)
    assert environment['SENTINELOPS_MODEL_STORE'] == str(model)
    assert environment['SENTINELOPS_MODEL_RELEASE_REF'] == release
    assert not model.exists()  # no fabricated bundle or approval is written
    arguments = lane_b.launch_arguments('incidents','stream',model,release)
    assert arguments[-4:] == ['--model-store',str(model),'--model-release-ref',release]
