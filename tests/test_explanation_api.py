from copy import deepcopy
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from sentinelops.api import create_app
from sentinelops.api_settings import Settings
from sentinelops.contracts import validate_payload
from sentinelops.explanations.service import ExplanationProvider, PROMPT_VERSION
from sentinelops.incidents.domain import IncidentNotFound

TOKEN = 'fixture-explanation-token-' + 'x' * 32
HEADERS = {'Authorization': 'Bearer ' + TOKEN}


class ReadOnlyRepository:
    def __init__(self):
        self.source = json.loads((Path(__file__).parents[1] / 'samples/incident-envelope.sample.json').read_text())
        self.reads = []

    async def check_ready(self): pass

    async def get(self, incident, revision):
        self.reads.append(('get', incident, revision))
        if incident != self.source['incident_id'] or revision not in (None, self.source['revision']):
            raise IncidentNotFound()
        return {k: deepcopy(v) for k, v in self.source.items() if k != 'timeline'}

    async def history(self, incident, **kwargs):
        self.reads.append(('history', incident, kwargs))
        return deepcopy(self.source['timeline']['events'])


def client(repo=None, provider=None):
    return TestClient(create_app(Settings(api_token=TOKEN), repository=repo or ReadOnlyRepository(),
                                 explanation_provider=provider))


def path(repo): return '/v1/incidents/' + repo.source['incident_id'] + '/explanation'


def test_real_backend_fallback_is_authenticated_pinned_and_read_only_without_models():
    repo = ReadOnlyRepository(); before = deepcopy(repo.source)
    with client(repo) as api:
        assert api.get(path(repo)).status_code == 401 and not repo.reads
        response = api.get(path(repo) + '?revision=1', headers={**HEADERS, 'If-None-Match': '"1"'})
        assert response.status_code == 200 and response.headers['x-incident-revision'] == '1'
        assert 'etag' not in response.headers and response.headers['cache-control'] == 'no-store'
        body = response.json(); validate_payload('explanation-response.schema.json', body)
        assert body['fallback'] and body['provider'] == 'deterministic-facts/v1'
        assert body['revision'] == 1 and 'not confirmed causes' in body['payload']['summary']
        assert 'availability=UNKNOWN' in body['payload']['summary']
        known = {e['id'] for e in repo.source['payload']['evidence']}
        assert all(set(c['evidence_refs']) <= known for c in body['payload']['claims'])
        assert api.get('/ready').status_code == 503  # Explanation does not require an activated model.
        assert repo.source == before
        assert repo.reads[1][2]['max_revision'] == 1
        assert set(api.get('/openapi.json').json()['paths'][path(repo).replace(repo.source['incident_id'], '{incident_id}')]) == {'get'}


def test_latest_resolution_pins_history_before_a_concurrent_current_revision_advance():
    repo = ReadOnlyRepository()
    original = repo.history
    async def history(incident, **kwargs):
        result = await original(incident, **kwargs)
        repo.source['revision'] = 2
        repo.source['payload']['evidence'][0]['fact'] = 'new-revision-only'
        return result
    repo.history = history
    with client(repo) as api:
        result = api.get(path(repo), headers=HEADERS)
    assert result.status_code == 200 and result.json()['revision'] == 1
    assert 'new-revision-only' not in result.text
    assert repo.reads[1][2]['max_revision'] == 1


@pytest.mark.parametrize('query', ['0', '-1', 'true', '9223372036854775808'])
def test_invalid_revision_never_reaches_a_snapshot_read(query):
    repo = ReadOnlyRepository()
    with client(repo) as api:
        assert api.get(path(repo) + '?revision=' + query, headers=HEADERS).status_code == 422
    assert not repo.reads


def test_not_found_and_database_outage_do_not_become_empty_explanations(caplog):
    repo = ReadOnlyRepository()
    with client(repo) as api:
        assert api.get(path(repo) + '?revision=99', headers=HEADERS).status_code == 404
    async def broken(*args, **kwargs): raise OSError('postgresql://secret:PRIVATE@internal/db')
    repo.history = broken
    with client(repo) as api:
        result = api.get(path(repo), headers=HEADERS)
        assert result.status_code == 503 and result.json()['code'] == 'control_store_unavailable'
        assert 'PRIVATE' not in result.text and 'PRIVATE' not in caplog.text


@pytest.mark.parametrize('mode', ['selection', 'failure', 'timeout', 'stale'])
def test_http_reuses_bounded_provider_and_same_revision_fallback(mode, caplog):
    import asyncio
    async def invoke(request):
        if mode == 'failure': raise RuntimeError('PRIVATE provider credential')
        if mode == 'timeout': await asyncio.sleep(1)
        return {'incident_id': request['incident_id'], 'revision': 999 if mode == 'stale' else request['revision'],
                'prompt_version': PROMPT_VERSION, 'card_ids': [request['cards'][0]['card_id']]}
    provider = ExplanationProvider('software-fixture/v1', invoke, timeout_seconds=.01)
    repo = ReadOnlyRepository()
    with client(repo, provider) as api:
        response = api.get(path(repo) + '?revision=1', headers=HEADERS)
    assert response.status_code == 200
    assert response.json()['fallback'] == (mode != 'selection')
    assert response.json()['revision'] == 1 and 'PRIVATE' not in caplog.text


@pytest.mark.parametrize('mutation', ['identity', 'reference', 'fallback', 'extra', 'shape'])
def test_backend_contract_drift_is_rejected_as_unavailable(monkeypatch, mutation):
    from sentinelops.explanations.service import explain_incident
    async def drift(envelope, **kwargs):
        result = await explain_incident(envelope)
        if mutation == 'identity': result['payload']['incident_id'] = 'foreign'
        elif mutation == 'reference': result['payload']['claims'][0]['evidence_refs'] = ['foreign']
        elif mutation == 'fallback': result['fallback'] = False
        elif mutation == 'extra': result['revision'] = 999
        else: return None
        return result
    monkeypatch.setattr('sentinelops.api.explain_incident', drift)
    repo = ReadOnlyRepository()
    with client(repo) as api:
        response = api.get(path(repo), headers=HEADERS)
    assert response.status_code == 503 and response.json()['code'] == 'explanation_unavailable'


def test_wrapper_sample_and_nested_identity_fallback_utc_contract():
    sample = json.loads((Path(__file__).parents[1] / 'samples/explanation-response.sample.json').read_text())
    validate_payload('explanation-response.schema.json', sample)
    for changes in ({'revision': 0}, {'generated_at': '2026-01-01T01:00:00+01:00'},
                    {'fallback': False}, {'unexpected': True}):
        with pytest.raises(ValueError): validate_payload('explanation-response.schema.json', {**sample, **changes})
