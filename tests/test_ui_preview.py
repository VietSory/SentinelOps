"""Local fixture HTTP contracts, same shipped UI, and no production I/O."""
from datetime import datetime, timedelta, timezone
import subprocess

import asyncpg
from fastapi.testclient import TestClient
import pytest

from scripts import check_operator_ui as fixtures
from scripts.ui_preview import server
from sentinelops.contracts import utc_time, validate_payload
from sentinelops.ui.routes import ASSETS, ASSET_NAMES, HEADERS


@pytest.fixture
def preview(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Preview attempted production I/O or a subprocess')
    monkeypatch.setattr(asyncpg, 'connect', forbidden)
    monkeypatch.setattr(asyncpg, 'create_pool', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setenv('SENTINELOPS_DATABASE_URL', 'postgresql://do-not-connect.invalid/production')
    monkeypatch.setenv('SENTINELOPS_ENVIRONMENT', 'production')
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with TestClient(server.create_preview_app(now=now), base_url='http://127.0.0.1',
                    client=('127.0.0.1', 50000),
                    headers={'Authorization':'Bearer '+fixtures.FIXTURE_TOKEN}) as client:
        yield client, now


def test_preview_uses_real_ui_router_and_shipped_assets_with_explicit_warning(preview):
    client, _ = preview
    checks = fixtures.verify_ui_assets(client)
    assert checks['assets_checked'] == len(ASSET_NAMES)
    page = client.get('/ui/')
    assert server.WARNING in page.text and 'fixture-warning' in page.text
    assert page.text.replace(server.PREVIEW_META, '').replace(server.PREVIEW_BANNER, '') == (ASSETS/'index.html').read_text()
    assert len(page.content) == int(page.headers['content-length'])
    for name in ASSET_NAMES:
        assert client.get('/ui/assets/'+name).content == (ASSETS/name).read_bytes()
    assert 'DỮ LIỆU GIẢ' in client.get('/').text
    assert fixtures.FIXTURE_TOKEN in client.get('/').text
    assert client.get('/ui', follow_redirects=False).headers['location'] == '/ui/'
    for path in ('/', '/ui/', '/ui/assets/app.js', '/ready', '/v1/incidents'):
        response = client.get(path)
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-sentinelops-data'] == 'synthetic-fixture-only'
    for key, value in HEADERS.items():
        assert page.headers[key] == value


def test_preview_feed_covers_filter_summary_and_shared_fixture_records(preview):
    client, now = preview
    body = client.get('/v1/incidents').json()
    validate_payload('incident-list.schema.json', body)
    assert body['items'] == fixtures.fixture_records(now) and body['next_cursor'] is None
    assert len(body['items']) == 8
    assert {row['severity'] for row in body['items']} == {'critical','high','medium','low'}
    assert {'recovered','escalated'} <= {row['state'] for row in body['items']}
    assert sum(utc_time(row['detected_at']) >= now-timedelta(hours=24) for row in body['items']) == 7
    assert max(utc_time(row['detected_at']) for row in body['items'])-min(utc_time(row['detected_at']) for row in body['items']) > timedelta(hours=24)
    first = client.get('/v1/incidents?limit=3').json()
    second = client.get('/v1/incidents', params={'limit':3,'cursor':first['next_cursor']}).json()
    third = client.get('/v1/incidents', params={'limit':3,'cursor':second['next_cursor']}).json()
    assert first['items']+second['items']+third['items'] == body['items']
    assert third['next_cursor'] is None
    filtered = client.get('/v1/incidents?state=recovered&service=payment-service').json()['items']
    assert [row['incident_id'] for row in filtered] == ['INC-FIXTURE-D']


def test_every_incident_context_matches_feed_and_schema(preview):
    client, now = preview
    for row in fixtures.fixture_records(now):
        body = client.get('/v1/incidents/'+row['incident_id']+'/context').json()
        validate_payload('incident-envelope.schema.json', body)
        assert body == fixtures.fixture_envelope(row['incident_id'], row['revision'], now=now)
        assert all(body['payload'][key] == row[key] for key in
                   ('incident_id','state','severity','detected_at','affected_services'))
        if row['state'] == 'recovered':
            assert body['context']['lifecycle_stage'] == 'RECOVERED'
    first = client.get('/v1/incidents/INC-FIXTURE-A/context').json()
    second = client.get('/v1/incidents/INC-FIXTURE-A/context?after_revision=1&limit=1').json()
    third = client.get('/v1/incidents/INC-FIXTURE-A/context?after_revision=2').json()
    assert [event['revision'] for page in (first, second, third) for event in page['timeline']['events']] == [1,2,3]
    assert third['timeline']['next_after_revision'] is None
    assert client.get('/v1/incidents/INC-FIXTURE-A/context?revision=1').json()['revision'] == 1


@pytest.mark.parametrize('revision', [1,3])
def test_explanations_and_actions_reuse_existing_browser_contracts(preview, revision):
    client, now = preview
    base = '/v1/incidents/INC-FIXTURE-A'
    response = client.get(base+'/explanation', params={'revision':revision})
    assert response.headers['x-incident-revision'] == str(revision)
    validate_payload('explanation-response.schema.json', response.json())
    assert response.json() == fixtures.fixture_explanation('INC-FIXTURE-A', revision, now=now)
    for cursor in (None, 'fixture-actions-2'):
        params = {'revision':revision, **({'cursor':cursor} if cursor else {})}
        body = client.get(base+'/actions', params=params).json()
        validate_payload('action-list.schema.json', body)
        assert body == fixtures.fixture_actions('INC-FIXTURE-A', revision, cursor, now=now)
    body = client.get('/v1/actions/'+'1'*64+'/quarantine').json()
    validate_payload('quarantine-status.schema.json', body)
    assert body == fixtures.fixture_quarantine('1'*64, now=now)
    assert not body['resolution']['payload']['dispatch_authorized']
    assert not body['resolution']['payload']['recovery_accepted']


def test_pending_proposal_is_shared_pinned_and_never_renewed_on_poll(preview):
    client, now = preview
    body = client.get('/v1/runbook-proposals?limit=100').json()
    assert body == {'items':fixtures.fixture_proposals(now), 'has_more':False}
    row = body['items'][0]
    assert row['status'] == 'pending' and row['current_revision'] == row['proposal']['incident_revision']
    assert row['incident'] == client.get('/v1/incidents/INC-FIXTURE-A/context').json()['payload']
    assert now < utc_time(row['proposal']['expires_at']) <= now+timedelta(minutes=15)
    assert utc_time(row['incident']['evidence'][-1]['observed_at']) < now
    for _ in range(3):
        assert client.get('/v1/runbook-proposals?incident_id=INC-FIXTURE-A').json() == body
    assert client.get('/v1/runbook-proposals?incident_id=INC-FIXTURE-B').json()['items'] == []
    with TestClient(server.create_preview_app(now=now-timedelta(minutes=15)),
                    client=('127.0.0.1', 50000), headers=client.headers) as expired:
        assert expired.get('/v1/runbook-proposals').json()['items'][0]['status'] == 'expired'


def test_readiness_is_explicitly_synthetic_and_auth_uses_only_fake_token(preview):
    client, _ = preview
    response = client.get('/ready')
    assert response.status_code == 200 and response.json()['fixture_mode']
    assert response.json()['warning'] == server.WARNING
    assert not response.json()['direct_inference']
    assert client.get('/ui/auth-config').json() == {'mode':'manual'}
    assert client.get('/ready/incidents').json()['status'] == 'ready'
    for token in ('', 'real-operator-token-must-not-be-used-here'):
        for path in ('/ready/incidents', '/v1/incidents', '/v1/runbook-proposals'):
            assert client.get(path, headers={'Authorization':token}).status_code == 401


@pytest.mark.parametrize('path', ['/preview/grafana/d/sentinelops-slo?from=1&to=2',
                                  '/preview/jaeger/search?service=payment-service',
                                  '/preview/jaeger/trace/'+'1'*32])
def test_observability_links_stay_on_explicit_local_fixture_pages(preview, path):
    client, _ = preview
    response = client.get(path)
    assert response.status_code == 200 and server.WARNING in response.text
    assert 'Không có dashboard hay trace thật' in response.text


@pytest.mark.parametrize('method', ['post','put','patch','delete'])
@pytest.mark.parametrize('path', ['/v1/incidents', '/v1/runbook-proposals/'+'a'*64+'/decision',
                                  '/v1/actions/'+'1'*64+'/quarantine', '/ready'])
def test_preview_rejects_every_write(preview, method, path):
    client, _ = preview
    response = getattr(client, method)(path)
    assert response.status_code == 405 and response.json()['code'] == 'fixture_read_only'


@pytest.mark.parametrize('path,status', [
    ('/v1/incidents?cursor=garbage',422), ('/v1/incidents?limit=0',422),
    ('/v1/incidents/INC-FIXTURE-A/context?revision=0',422),
    ('/v1/incidents/INC-FIXTURE-A/context?revision=4',404),
    ('/v1/incidents/unknown/context',404),
    ('/v1/incidents/INC-FIXTURE-B/explanation?revision=2',404),
    ('/v1/incidents/INC-FIXTURE-A/actions?revision=4',404),
    ('/v1/incidents/INC-FIXTURE-A/actions?revision=3&cursor=garbage',422),
    ('/v1/actions/unknown/quarantine',404), ('/v1/runbook-proposals?limit=101',422),
])
def test_invalid_inputs_never_fabricate_incidents_or_unbounded_pages(preview, path, status):
    client, _ = preview
    assert client.get(path).status_code == status


def test_launcher_binds_only_loopback_and_rejects_host_override(monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(server.uvicorn, 'run', lambda app, **kwargs: calls.append(kwargs))
    server.main(['--port','18081'])
    assert calls == [{'host':'127.0.0.1','port':18081,'access_log':False}]
    assert server.WARNING in caplog.text
    with pytest.raises(SystemExit):
        server.main(['--host','0.0.0.0'])
    with pytest.raises(SystemExit):
        server.main(['--port','0'])
    assert len(calls) == 1


def test_even_an_alternate_launcher_cannot_serve_non_loopback_clients():
    with TestClient(server.create_preview_app(), client=('192.0.2.1', 50000)) as client:
        assert client.get('/ui/').status_code == 403
        assert client.get('/ready').status_code == 403
