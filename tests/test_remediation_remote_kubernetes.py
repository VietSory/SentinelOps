"""Remote transport fixtures only; no EKS execution or action grant."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import ssl

import certifi
import httpx
import pytest

from sentinelops.remediation.domain import Blocked, KubernetesUnavailable, Policy
from sentinelops.remediation.kubernetes import LocalKubernetes, configured_server, local_server
from test_remediation_kubernetes import BAD, GOOD, TARGET, api_objects, observation

SERVER = 'https://fixture.ap-southeast-1.eks.amazonaws.com'
ENV = 'SENTINELOPS_KUBERNETES_REMOTE_SERVER'
LOCAL_ERROR = 'rollback adapter requires an explicit credential-free local HTTPS API server'


def files(tmp_path):
    token = tmp_path / 'token'
    token.write_text('operator-fixture-token-' + 'x' * 32)
    token.chmod(0o600)
    ca = tmp_path / 'ca.crt'
    ca.write_bytes(Path(certifi.where()).read_bytes())
    return token, ca


@pytest.mark.parametrize('selected', [None, '', 'true', SERVER + ':443'])
def test_remote_requires_exact_endpoint_opt_in(monkeypatch, selected):
    monkeypatch.delenv(ENV, raising=False)
    if selected is not None:
        monkeypatch.setenv(ENV, selected)
    with pytest.raises(ValueError, match='^' + LOCAL_ERROR + '$'):
        configured_server(SERVER)
    assert configured_server('https://127.0.0.1:6443') == 'https://127.0.0.1:6443'


@pytest.mark.parametrize('endpoint', [
    'http://eks.example.com', 'https://', 'https://user@eks.example.com',
    'https://user:secret@eks.example.com', SERVER + '/extra', SERVER + '?', SERVER + '#',
    SERVER + '?token=secret', SERVER + '#fragment', SERVER + ':0', SERVER + ':65536',
    SERVER + ':invalid', SERVER + '\n', 'https://eks.example.com\\evil',
    'https://eks%2eexample.com',
])
def test_remote_opt_in_cannot_bypass_endpoint_safety(monkeypatch, endpoint):
    monkeypatch.setenv(ENV, endpoint)
    with pytest.raises(ValueError, match='credential-free remote HTTPS'):
        configured_server(endpoint)


def test_local_validator_remains_loopback_only_even_with_remote_opt_in(monkeypatch):
    monkeypatch.setenv(ENV, SERVER)
    with pytest.raises(ValueError, match='^' + LOCAL_ERROR + '$'):
        local_server(SERVER)


@pytest.mark.parametrize('problem', ['missing-token', 'public-token', 'symlink-token', 'invalid-token', 'missing-ca', 'invalid-ca'])
def test_remote_requires_private_token_and_valid_explicit_ca(tmp_path, monkeypatch, problem):
    monkeypatch.setenv(ENV, SERVER)
    token, ca = files(tmp_path)
    if problem == 'missing-token': token = tmp_path / 'absent'
    if problem == 'public-token': token.chmod(0o644)
    if problem == 'symlink-token':
        link = tmp_path / 'link'; link.symlink_to(token); token = link
    if problem == 'invalid-token': token.write_text('short')
    if problem == 'missing-ca': ca = tmp_path / 'absent-ca'
    if problem == 'invalid-ca': ca.write_text('not a certificate')
    with pytest.raises((ValueError, OSError)):
        LocalKubernetes(SERVER, TARGET, token_file=token, ca_file=ca)


def test_remote_transport_verifies_tls_and_only_replaces_digest_image(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, SERVER)
    token, ca = files(tmp_path)
    contexts = []
    create = ssl.create_default_context
    def context(**kwargs):
        assert kwargs == {'cafile': str(ca)}
        result = create(**kwargs); contexts.append(result); return result
    monkeypatch.setattr(ssl, 'create_default_context', context)
    deployment, sets, pods = api_objects()
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.host == 'fixture.ap-southeast-1.eks.amazonaws.com'
        assert request.headers['Authorization'] == 'Bearer ' + token.read_text()
        path = request.url.path
        if request.method == 'PATCH':
            operations = json.loads(request.content)
            assert [p for p in operations if p['op'] != 'test'] == [
                {'op': 'replace', 'path': '/spec/template/spec/containers/0/image', 'value': GOOD}]
            body = deepcopy(deployment)
            body['spec']['template']['spec']['containers'][0]['image'] = GOOD
        elif path.endswith('/kube-system'): body = {'metadata': {'uid': TARGET.cluster_uid}}
        elif path.endswith('/namespaces/' + TARGET.namespace):
            body = {'metadata': {'uid': TARGET.namespace_uid, 'labels': {'sentinelops.dev/lane': 'B'}}}
        elif path.endswith('/replicasets'): body = sets
        elif path.endswith('/pods'): body = pods
        elif path.endswith('/horizontalpodautoscalers'): body = {'items': []}
        else: body = deployment
        return httpx.Response(200, json=body)
    async def run():
        kube = LocalKubernetes(SERVER, TARGET, token_file=token, ca_file=ca,
                               transport=httpx.MockTransport(handler))
        try:
            before = await kube.observe()
            before.validate(Policy(TARGET))
            assert before.image == BAD
            await kube.patch(before, GOOD, dry_run=True)
            await kube.patch(before, GOOD, dry_run=False)
            for invalid in (BAD, 'payment:latest', 'other/payment@sha256:' + 'a' * 64):
                with pytest.raises(Blocked): await kube.patch(before, invalid, dry_run=False)
        finally: await kube.close()
    asyncio.run(run())
    assert contexts[0].verify_mode == ssl.CERT_REQUIRED and contexts[0].check_hostname
    patches = [r for r in calls if r.method == 'PATCH']
    assert len(patches) == 2 and patches[0].url.params['dryRun'] == 'All'
    assert not patches[1].url.params


@pytest.mark.parametrize('failure', ['tls', 'redirect', 'rbac'])
def test_remote_failures_have_no_fallback_or_retry(tmp_path, monkeypatch, failure):
    monkeypatch.setenv(ENV, SERVER)
    token, ca = files(tmp_path)
    calls = []
    def handler(request):
        calls.append(request)
        if failure == 'tls': raise httpx.ConnectError('private TLS provider detail', request=request)
        if failure == 'redirect': return httpx.Response(307, headers={'Location': 'https://other.example.com'})
        return httpx.Response(403, json={'message': 'private provider detail'})
    async def run():
        kube = LocalKubernetes(SERVER, TARGET, token_file=token, ca_file=ca,
                               transport=httpx.MockTransport(handler))
        try:
            with pytest.raises((Blocked, KubernetesUnavailable)) as caught:
                await kube.patch(observation(), GOOD, dry_run=True)
            assert 'private' not in str(caught.value)
        finally: await kube.close()
    asyncio.run(run())
    assert len(calls) == 1
