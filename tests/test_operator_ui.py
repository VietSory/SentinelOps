from fastapi.testclient import TestClient
from sentinelops.api import create_app
from sentinelops.api_settings import Settings
import pytest


@pytest.mark.parametrize('revision,after',[(1,0),(3,0),(3,1)])
def test_browser_fixtures_preserve_the_existing_envelope_contract(revision,after):
    from scripts.check_operator_ui import fixture_envelope
    from sentinelops.contracts import validate_payload
    validate_payload('incident-envelope.schema.json',fixture_envelope('INC-FIXTURE-A',revision,after))


def test_ui_serves_same_origin_without_exposing_token_or_private_incidents():
    token = 'PRIVATE-OPERATOR-TOKEN-'+'x'*32
    with TestClient(create_app(Settings(api_token=token))) as client:
        response = client.get('/ui/')
        assert response.status_code == 200 and 'text/html' in response.headers['content-type']
        assert token not in response.text and 'Connect to your workspace' in response.text
        assert "connect-src 'self'" in response.headers['content-security-policy']
        assert "frame-ancestors 'none'" in response.headers['content-security-policy']
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['cache-control'] == 'no-store'
        assert client.get('/v1/incidents').status_code == 401
        assert client.head('/ui/').status_code == 200
        assert client.get('/ui',follow_redirects=False).headers['location'] == '/ui/'
        for filename in ('app.js','client.js','workspace.js','styles.css'):
            asset = client.get('/ui/assets/'+filename)
            assert asset.status_code == 200 and token not in asset.text
        for filename in ('index.html','api-token','routes.py','..%2F..%2Fapi_settings.py'):
            assert client.get('/ui/assets/'+filename).status_code == 404
        assert '/ui/' not in client.get('/openapi.json').json()['paths']


def test_ui_still_loads_when_dependencies_are_unavailable():
    with TestClient(create_app(Settings())) as client:
        assert client.get('/ui/').status_code == 200
        assert client.get('/ready').status_code == 503
        assert client.get('/ready/incidents').status_code == 503
        assert client.get('/health').status_code == 200
