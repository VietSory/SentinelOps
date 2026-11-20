import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/aws_demo/deploy_llm_explanation.sh'
IMAGE = 'example.com/operator-ui@sha256:' + 'a' * 64
ORIGINAL_IMAGE = 'example.com/operator-ui@sha256:' + 'b' * 64
KEY = 'SENTINELOPS_LLM_API_KEY'
BASE = 'SENTINELOPS_LLM_BASE_URL'
MODEL = 'SENTINELOPS_LLM_MODEL'
ACTOR = 'SENTINELOPS_LLM_ACTOR_AUTHORIZATION'
UI_PATH = '/spec/template/spec/containers/1'
ENV_PATH = UI_PATH + '/env'
IMAGE_PATH = UI_PATH + '/image'
SECRET_REF = {'name': KEY, 'valueFrom': {'secretKeyRef': {
    'name': 'sentinelops-llm', 'key': KEY, 'optional': False,
}}}
OLD_BASE = {'name': BASE, 'value': 'https://previous.example.com/v1'}
OLD_ACTOR = {'name': ACTOR, 'valueFrom': {'secretKeyRef': {
    'name': 'legacy-actor', 'key': ACTOR,
}}}
NEW_BASE = {'name': BASE, 'value': 'https://api.openai.com/v1'}
NEW_MODEL = {'name': MODEL, 'value': 'requested-model'}
EXISTING = {'name': 'EXISTING', 'value': 'preserve-me'}
OTHER = {'name': 'DATABASE_TOKEN', 'valueFrom': {'secretKeyRef': {
    'name': 'database', 'key': 'token',
}}}

MOCK_KUBECTL = r'''
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

argv = sys.argv[1:]
cluster_path = Path(os.environ['MOCK_CLUSTER'])
cluster = json.loads(cluster_path.read_text())
deployment = cluster['deployment']
metadata = deployment['metadata']
revision_key = 'deployment.kubernetes.io/revision'
record = {'argv': argv}
if argv == ['config', 'current-context']:
    args = argv
else:
    assert argv[:2] == ['--context=sentinelops-demo', '--namespace=sentinelops-b-actions']
    args = argv[2:]
if args[0] == 'patch':
    record['patch'] = json.load(sys.stdin)
    snapshot = Path(os.environ['MOCK_STATE_DIR']) / 'rollback.json'
    assert snapshot.is_file() and snapshot.stat().st_mode & 0o777 == 0o600
with open(os.environ['MOCK_LOG'], 'a') as log:
    print(json.dumps(record), file=log)

if args == ['config', 'current-context']:
    print(cluster['context'])
elif args == ['get', 'namespace', 'sentinelops-b-actions', '-o', 'name']:
    print(cluster['namespace'])
elif args == ['get', 'secret', 'sentinelops-llm', '-o',
              'go-template={{range $name, $ignored := .data}}{{println $name}}{{end}}']:
    print(*cluster['secret_keys'], sep='\n')
elif args == ['get', 'deployment', 'remediation-controller', '-o', 'json']:
    print(json.dumps(deployment))
elif args == ['patch', 'deployment', 'remediation-controller', '--type=json', '--patch-file=/dev/stdin', '-o', 'json']:
    if cluster.get('reject_patch'):
        sys.exit(1)
    updated = deepcopy(deployment)
    for operation in record['patch']:
        path = operation['path'].split('/')[1:]
        target = updated
        for segment in path[:-1]:
            target = target[int(segment)] if isinstance(target, list) else target[segment]
        field = int(path[-1]) if isinstance(target, list) and path[-1] != '-' else path[-1]
        if operation['op'] == 'test':
            assert target[field] == operation['value']
        elif operation['op'] == 'remove':
            del target[field]
        elif operation['op'] == 'add' and field == '-':
            target.append(operation['value'])
        elif operation['op'] == 'replace' or operation['op'] == 'add' and isinstance(target, dict):
            target[field] = operation['value']
        else:
            raise AssertionError(operation)
    if deployment['spec'] != updated['spec']:
        updated['metadata']['generation'] += 1
    updated['metadata']['resourceVersion'] = str(int(metadata['resourceVersion']) + 1)
    cluster['deployment'] = updated
    cluster_path.write_text(json.dumps(cluster))
    print(json.dumps(updated))
elif args == ['rollout', 'status', 'deployment/remediation-controller', '--timeout=300s']:
    if cluster.get('reject_rollout'):
        sys.exit(1)
    metadata['resourceVersion'] = str(int(metadata['resourceVersion']) + 1)
    metadata['annotations'][revision_key] = str(metadata['generation'])
    cluster['pod_containers'] = deepcopy(deployment['spec']['template']['spec']['containers'])
    if cluster.get('mutate_pod_env'):
        ui = cluster['pod_containers'][1]
        ui.setdefault('env', []).append({'name': 'SENTINELOPS_LLM_ACTOR_AUTHORIZATION', 'value': 'unexpected-fixture'})
    if cluster.get('mutate_pod_model'):
        for entry in cluster['pod_containers'][1]['env']:
            if entry['name'] == 'SENTINELOPS_LLM_MODEL':
                entry['value'] = 'unexpected-model'
    if cluster.get('mutate_pod_image'):
        cluster['pod_containers'][2]['image'] = 'unexpected-image'
    cluster_path.write_text(json.dumps(cluster))
    print('rollout complete')
elif args == ['get', 'replicasets', '-l', 'app=remediation-controller', '-o', 'json']:
    def replica_set(uid, revision, owner):
        return {'metadata': {'uid': uid, 'annotations': {revision_key: revision},
                'ownerReferences': [{'controller': True, 'kind': 'Deployment', 'uid': owner}]},
                'spec': {'selector': {'matchLabels': {'pod-template-hash': uid}}}}

    revision = metadata['annotations'][revision_key]
    print(json.dumps({'items': [replica_set('old', '1', metadata['uid']),
        replica_set('unrelated', revision, 'another-deployment'),
        replica_set('active', revision, metadata['uid'])]}))
elif args == ['get', 'pods', '-l', 'pod-template-hash=active', '-o', 'json']:
    def pod(name, owner, ready=True):
        return {'metadata': {'name': name,
                'ownerReferences': [{'controller': True, 'kind': 'ReplicaSet', 'uid': owner}]},
                'status': {'conditions': [{'type': 'Ready', 'status': 'True' if ready else 'False'}]}}

    print(json.dumps({'items': [pod('old-pod', 'old'),
        pod('active-pod', 'active', not cluster.get('pod_unready'))]}))
elif args[:3] == ['get', 'pod', 'active-pod']:
    assert args[3:] == ['-o', r'jsonpath={.spec.containers[?(@.name=="operator-ui")].env}{"\n"}'
                             r'{.spec.containers[*].name}{"\n"}{.spec.containers[*].image}']
    containers = cluster['pod_containers']
    print(json.dumps(containers[1]['env'], separators=(',', ':')) if containers[1].get('env') else '')
    print(*(item['name'] for item in containers))
    print(*(item['image'] for item in containers), end='')
else:
    raise AssertionError(args)
'''


@pytest.fixture
def cluster(tmp_path):
    mock_bin = tmp_path / 'bin'
    mock_bin.mkdir()
    kubectl = mock_bin / 'kubectl'
    kubectl.write_text(f'#!{sys.executable}\n{MOCK_KUBECTL}')
    kubectl.chmod(0o700)
    cluster_file = tmp_path / 'cluster.json'
    log = tmp_path / 'argv.jsonl'

    def write(data):
        cluster_file.write_text(json.dumps(data))

    def read():
        return json.loads(cluster_file.read_text())

    write({
        'context': 'sentinelops-demo', 'namespace': 'namespace/sentinelops-b-actions',
        'secret_keys': [KEY],
        'deployment': {
            'metadata': {'uid': 'deployment-uid', 'resourceVersion': '7', 'generation': 3,
                         'annotations': {'deployment.kubernetes.io/revision': '3'}},
            'spec': {'replicas': 1, 'strategy': {'type': 'Recreate'}, 'template': {'spec': {'containers': [
                {'name': 'remediation-controller', 'image': 'controller-image'},
                {'name': 'operator-ui', 'image': ORIGINAL_IMAGE, 'env': [EXISTING, OLD_BASE, OLD_ACTOR, OTHER]},
                {'name': 'oom-demo-controller', 'image': 'oom-image'},
            ]}}},
        },
    })

    def run(*options, execute=False, rollback=False):
        state_dir = tmp_path / 'rollback state'
        action = 'rollback' if rollback else 'deploy'
        log.write_text('')
        env = {**os.environ, 'PATH': f'{mock_bin}{os.pathsep}{os.environ["PATH"]}',
               'AWS_PROFILE': 'console', 'LLM_DEPLOY_EXECUTE': '1' if execute else '0',
               'MOCK_LOG': str(log), 'MOCK_CLUSTER': str(cluster_file), 'MOCK_STATE_DIR': str(state_dir)}
        result = subprocess.run(
            ['bash', str(SCRIPT), action, '--state-dir', str(state_dir), *options],
            env=env, capture_output=True, text=True, check=False,
        )
        return result, [json.loads(line) for line in log.read_text().splitlines()], state_dir / 'rollback.json'

    return run, read, write


def containers_of(state):
    return state['deployment']['spec']['template']['spec']['containers']


def patches(records):
    return [record['patch'] for record in records if 'patch' in record]


def patch_header(resource_version, old_image, new_image):
    return [
        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': resource_version},
        {'op': 'test', 'path': UI_PATH + '/name', 'value': 'operator-ui'},
        {'op': 'test', 'path': IMAGE_PATH, 'value': old_image},
        {'op': 'replace', 'path': IMAGE_PATH, 'value': new_image},
    ]


def test_dry_run_uses_only_reads(cluster):
    run, read, _ = cluster
    initial = read()
    result, records, snapshot = run('--image', IMAGE, '--llm-model', 'requested-model')
    assert result.returncode == 0, result.stderr
    assert 'DRY-RUN' in result.stdout
    assert [record['argv'][2:4] for record in records[1:]] == [
        ['get', 'namespace'], ['get', 'deployment'], ['get', 'secret'],
    ]
    assert not snapshot.parent.exists()
    assert read() == initial


def test_execute_default_openai_and_rollback(cluster):
    run, read, _ = cluster
    original = containers_of(read())
    result, records, snapshot = run('--image', IMAGE, '--llm-model', 'requested-model', execute=True)
    assert result.returncode == 0, result.stderr
    assert patches(records) == [patch_header('7', ORIGINAL_IMAGE, IMAGE) + [
        {'op': 'replace', 'path': ENV_PATH + '/1', 'value': NEW_BASE},
        {'op': 'remove', 'path': ENV_PATH + '/2'},
        {'op': 'add', 'path': ENV_PATH + '/-', 'value': NEW_MODEL},
        {'op': 'add', 'path': ENV_PATH + '/-', 'value': SECRET_REF},
    ]]
    assert records[-1]['argv'][2:5] == ['get', 'pod', 'active-pod']
    assert result.stdout.splitlines()[-3:] == [
        'remediation-controller\tcontroller-image', 'operator-ui\t' + IMAGE, 'oom-demo-controller\toom-image',
    ]
    deployed = containers_of(read())
    assert deployed[::2] == original[::2]
    assert deployed[1]['env'] == [EXISTING, NEW_BASE, OTHER, NEW_MODEL, SECRET_REF]
    assert json.loads(snapshot.read_text()) == {
        'image': ORIGINAL_IMAGE, 'env': [OLD_BASE, OLD_ACTOR], 'resourceVersion': '8', 'generation': 4,
    }
    assert snapshot.stat().st_mode & 0o777 == 0o600

    dry_run, records, _ = run(rollback=True)
    assert dry_run.returncode == 0, dry_run.stderr
    assert not patches(records)
    assert containers_of(read()) == deployed

    result, records, _ = run(execute=True, rollback=True)
    assert result.returncode == 0, result.stderr
    assert patches(records) == [patch_header('9', IMAGE, ORIGINAL_IMAGE) + [
        {'op': 'replace', 'path': ENV_PATH + '/1', 'value': OLD_BASE},
        {'op': 'remove', 'path': ENV_PATH + '/4'},
        {'op': 'remove', 'path': ENV_PATH + '/3'},
        {'op': 'add', 'path': ENV_PATH + '/-', 'value': OLD_ACTOR},
    ]]
    assert records[-1]['argv'][2:5] == ['get', 'pod', 'active-pod']
    restored = containers_of(read())
    assert restored[::2] == original[::2]
    assert restored[1]['image'] == ORIGINAL_IMAGE
    assert restored[1]['env'] == [EXISTING, OLD_BASE, OTHER, OLD_ACTOR]


def test_force_rollback_uses_current_version_and_preserves_other_changes(cluster):
    run, read, write = cluster
    result, _, _ = run('--image', IMAGE, '--llm-model', 'requested-model', execute=True)
    assert result.returncode == 0, result.stderr
    state = read()
    state['deployment']['metadata'].update(generation=5, resourceVersion='10')
    added = {'name': 'ADDED_LATER', 'value': 'must-survive'}
    containers_of(state)[1]['env'].insert(0, added)
    containers_of(state)[2]['image'] = 'later-oom-image'
    write(state)

    result, records, _ = run(execute=True, rollback=True)
    assert result.returncode != 0 and 'generation changed' in result.stderr
    assert not patches(records) and read() == state

    result, records, _ = run('--force', execute=True, rollback=True)
    assert result.returncode == 0, result.stderr
    assert len(patches(records)) == 1
    assert patches(records)[0][:4] == patch_header('10', IMAGE, ORIGINAL_IMAGE)
    assert containers_of(read())[1]['env'] == [added, EXISTING, OLD_BASE, OTHER, OLD_ACTOR]
    assert containers_of(read())[2]['image'] == 'later-oom-image'


@pytest.mark.parametrize(('options', 'message'), [
    (('--image', 'example.com/operator-ui:latest', '--llm-model', 'model'), 'digest-pinned'),
    (('--image', IMAGE), '--llm-model'),
    (('--image', IMAGE, '--llm-model', 'model', '--llm-base-url', 'http://api.openai.com/v1'), 'HTTPS'),
    (('--image', IMAGE, '--llm-model', 'model', '--force'), 'only valid for rollback'),
])
def test_invalid_arguments_make_no_calls(cluster, options, message):
    run, _, _ = cluster
    result, records, snapshot = run(*options, execute=True)
    assert result.returncode != 0 and message in result.stderr
    assert not records and not snapshot.exists()


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('context', 'wrong-context', 'current context'),
    ('namespace', 'namespace/wrong-namespace', 'namespace'),
    ('secret_keys', [], 'missing key'),
])
def test_preflight_refuses_wrong_target_or_missing_key(cluster, field, value, message):
    run, read, write = cluster
    state = read()
    state[field] = value
    write(state)
    result, records, snapshot = run('--image', IMAGE, '--llm-model', 'model', execute=True)
    assert result.returncode != 0 and message in result.stderr
    assert not patches(records) and not snapshot.exists()


def test_preflight_refuses_unexpected_container_layout(cluster):
    run, read, write = cluster
    state = read()
    containers = containers_of(state)
    containers[1], containers[2] = containers[2], containers[1]
    write(state)
    result, records, snapshot = run('--image', IMAGE, '--llm-model', 'model', execute=True)
    assert result.returncode != 0 and 'unexpected container layout' in result.stderr
    assert not patches(records) and not snapshot.exists()


@pytest.mark.parametrize('name', [KEY, ACTOR, 'CUSTOM_SECRET', 'OTHER_TOKEN'])
def test_preflight_refuses_sensitive_literals_without_printing_them(cluster, name):
    run, read, write = cluster
    state = read()
    ui = containers_of(state)[1]
    ui['env'] = [entry for entry in ui['env'] if entry['name'] != name]
    ui['env'].append({'name': name, 'value': 'sensitive-literal-fixture'})
    write(state)
    result, records, snapshot = run('--image', IMAGE, '--llm-model', 'model', execute=True)
    assert result.returncode != 0 and 'sensitive literal env entry' in result.stderr
    assert 'sensitive-literal-fixture' not in result.stdout + result.stderr + json.dumps(records)
    assert not patches(records) and not snapshot.exists()


def test_deploy_when_env_array_is_absent(cluster):
    run, read, write = cluster
    state = read()
    del containers_of(state)[1]['env']
    write(state)
    new_url = 'https://custom.example.com/v1'
    result, records, snapshot = run('--image', IMAGE, '--llm-model', 'requested-model',
                                    '--llm-base-url', new_url, execute=True)
    assert result.returncode == 0, result.stderr
    assert patches(records) == [patch_header('7', ORIGINAL_IMAGE, IMAGE) + [
        {'op': 'add', 'path': ENV_PATH, 'value': [{**NEW_BASE, 'value': new_url}, NEW_MODEL, SECRET_REF]},
    ]]
    assert json.loads(snapshot.read_text())['env'] == []
    result, records, _ = run(rollback=True, execute=True)
    assert result.returncode == 0, result.stderr
    assert not containers_of(read())[1].get('env')


def test_rejected_patch_leaves_cluster_and_snapshot_unconfirmed(cluster):
    run, read, write = cluster
    initial = read()
    initial['reject_patch'] = True
    write(initial)
    result, records, snapshot = run('--image', IMAGE, '--llm-model', 'requested-model', execute=True)
    assert result.returncode != 0 and 'confirmed result' in result.stderr
    assert len(patches(records)) == 1 and read() == initial
    assert json.loads(snapshot.read_text())['generation'] is None

    result, records, _ = run('--force', execute=True, rollback=True)
    assert result.returncode != 0 and 'unconfirmed rollback.json' in result.stderr
    assert not patches(records)


def test_confirmed_snapshot_survives_failed_rollout(cluster):
    run, read, write = cluster
    state = read()
    state['reject_rollout'] = True
    write(state)
    result, _, snapshot = run('--image', IMAGE, '--llm-model', 'requested-model', execute=True)
    assert result.returncode != 0
    assert json.loads(snapshot.read_text())['generation'] == 4
    state = read()
    state.pop('reject_rollout')
    write(state)
    result, _, _ = run(execute=True, rollback=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('change', ['mutate_pod_env', 'mutate_pod_model', 'mutate_pod_image', 'pod_unready'])
def test_deploy_detects_mismatch_on_actual_pod(cluster, change):
    run, read, write = cluster
    state = read()
    state[change] = True
    write(state)
    result, _, _ = run('--image', IMAGE, '--llm-model', 'requested-model', execute=True)
    assert result.returncode != 0 and 'Pod' in result.stderr


def test_rollback_detects_mismatch_on_actual_pod(cluster):
    run, read, write = cluster
    result, _, _ = run('--image', IMAGE, '--llm-model', 'requested-model', execute=True)
    assert result.returncode == 0, result.stderr
    state = read()
    state['mutate_pod_env'] = True
    write(state)
    result, records, _ = run(execute=True, rollback=True)
    assert result.returncode != 0 and 'differ on Pod active-pod' in result.stderr
    assert len(patches(records)) == 1
