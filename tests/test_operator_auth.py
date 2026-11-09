from copy import deepcopy
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from sentinelops.api import create_app
from sentinelops.api_settings import Settings

PRODUCER = 'fixture-producer-' + 'p' * 40
OPERATOR = 'fixture-operator-' + 'o' * 40


class Repository:
    def __init__(self):
        self.snapshot = json.loads((Path(__file__).parents[1] / 'samples/incident-envelope.sample.json').read_text())
        self.ready_calls = self.write_calls = 0
    async def check_ready(self): self.ready_calls += 1
    async def get(self, incident, revision=None):
        assert incident == self.snapshot['incident_id']
        return {k: deepcopy(v) for k, v in self.snapshot.items() if k != 'timeline'}
    async def list_current(self, **kwargs): return [await self.get(self.snapshot['incident_id'])]
    async def history(self, incident, **kwargs): return deepcopy(self.snapshot['timeline']['events'])
    async def write(self, request):
        assert request.actor == 'local-trusted-producer'
        self.write_calls += 1
        return await self.get(self.snapshot['incident_id'])


def auth(token): return {'Authorization': 'Bearer ' + token}


@pytest.mark.parametrize('producer', [None, PRODUCER])
def test_operator_can_read_existing_interfaces_with_models_unavailable(producer):
    repo = Repository(); before = deepcopy(repo.snapshot)
    app = create_app(Settings(api_token=producer, operator_token=OPERATOR), repository=repo)
    with TestClient(app) as api:
        root = '/v1/incidents/' + repo.snapshot['incident_id']
        for path in ('/ready/incidents', '/v1/incidents', root, root + '/context', root + '/explanation?revision=1'):
            assert api.get(path, headers=auth(OPERATOR)).status_code == 200
        assert api.get('/ready').json()['dependencies']['authentication'] is True
        assert api.get('/ready').status_code == 503
        assert api.get(root + '/actions', headers=auth(OPERATOR)).json()['code'] == 'action_store_unavailable'
        assert repo.snapshot == before and repo.write_calls == 0


@pytest.mark.parametrize('producer', [None, PRODUCER])
@pytest.mark.parametrize('suffix', ['', '/revisions'])
def test_operator_write_denial_precedes_repository_access_even_with_forged_role(producer, suffix):
    repo = Repository()
    with TestClient(create_app(Settings(api_token=producer, operator_token=OPERATOR), repository=repo)) as api:
        path = '/v1/incidents' + ('/' + repo.snapshot['incident_id'] + suffix if suffix else '')
        result = api.post(path, headers={**auth(OPERATOR), 'X-Role': 'producer', 'If-Match': '"0"',
                                        'Idempotency-Key': 'fixture-forged-role'}, json={})
        assert result.status_code == 403 and result.json()['code'] == 'read_only_credential'
        assert result.json()['request_id'] == result.headers['x-request-id']
        assert 'www-authenticate' not in result.headers
    assert repo.ready_calls == repo.write_calls == 0


def test_existing_producer_can_still_read_import_and_revise_with_original_actor():
    repo = Repository()
    with TestClient(create_app(Settings(api_token=PRODUCER, operator_token=OPERATOR), repository=repo)) as api:
        headers = {**auth(PRODUCER), 'If-Match': '"0"', 'Idempotency-Key': 'fixture-import'}
        assert api.post('/v1/incidents', headers=headers, json=repo.snapshot['payload']).status_code == 201
        body = json.loads((Path(__file__).parents[1] / 'samples/incident-write.sample.json').read_text())
        headers['If-Match'] = '"1"'
        assert api.post('/v1/incidents/' + body['payload']['incident_id'] + '/revisions', headers=headers, json=body).status_code == 200
        assert api.get('/ready/incidents', headers=auth(PRODUCER)).status_code == 200
    assert repo.write_calls == 2


@pytest.mark.parametrize('headers', [{}, auth('unknown-fixture-token'), {'Authorization': 'Basic ' + OPERATOR}])
def test_invalid_credentials_are_401_when_only_operator_auth_is_configured(headers):
    repo = Repository()
    with TestClient(create_app(Settings(operator_token=OPERATOR), repository=repo)) as api:
        response = api.get('/ready/incidents', headers=headers)
        assert response.status_code == 401 and response.headers['www-authenticate'] == 'Bearer'
    assert repo.ready_calls == 0


def test_settings_keep_secrets_private_distinct_and_explicitly_loaded(monkeypatch):
    for value in ('short', '', True):
        with pytest.raises(ValueError): Settings(operator_token=value)
    with pytest.raises(ValueError, match='distinct'): Settings(api_token=OPERATOR, operator_token=OPERATOR)
    monkeypatch.setenv('SENTINELOPS_OPERATOR_TOKEN', OPERATOR)
    settings = Settings.from_env()
    assert settings.operator_token == OPERATOR and OPERATOR not in repr(settings)
    assert PRODUCER not in repr(Settings(api_token=PRODUCER, operator_token=OPERATOR))


def test_production_authentication_remains_unavailable_for_both_credentials():
    repo = Repository()
    with pytest.raises(ValueError, match='static bearer tokens'):
        Settings(environment='aws', api_token=PRODUCER, operator_token=OPERATOR)
    with TestClient(create_app(Settings(environment='aws'), repository=repo)) as api:
        for token in (PRODUCER, OPERATOR):
            result = api.get('/ready/incidents', headers=auth(token))
            assert result.status_code == 503 and result.json()['code'] == 'authentication_adapter_unavailable'
    assert repo.ready_calls == 0
