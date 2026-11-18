import asyncio
import json
import time

from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient
import jwt
import pytest

from sentinelops.api import create_app
from sentinelops.api_settings import Settings, oidc_from_env
from sentinelops.oidc import OidcConfig, OidcVerifier, ScopeMissing, TokenRejected, VerifierUnavailable

ISSUER, AUDIENCE = 'https://idp.example/pool', 'sentinelops-api'
RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk(private, kid, alg):
    algorithm = jwt.algorithms.RSAAlgorithm if alg == 'RS256' else jwt.algorithms.ECAlgorithm
    return {**json.loads(algorithm.to_jwk(private.public_key())), 'kid': kid, 'alg': alg, 'use': 'sig'}


JWKS = {'keys': [jwk(RSA_KEY, 'rsa-1', 'RS256'), jwk(EC_KEY, 'ec-1', 'ES256')]}


def token(scope='sentinelops/incidents.read', *, key=RSA_KEY, kid='rsa-1', alg='RS256', **claims):
    now = int(time.time())
    body = {'iss': ISSUER, 'aud': AUDIENCE, 'sub': 'svc-producer', 'iat': now, 'exp': now + 300, 'scope': scope}
    body.update(claims)
    return jwt.encode({k: v for k, v in body.items() if v is not None}, key, algorithm=alg, headers={'kid': kid})


def verifier(document=JWKS, calls=None, **config):
    async def fetch(url):
        if calls is not None:
            calls.append(url)
        if isinstance(document, Exception):
            raise document
        return document
    return OidcVerifier(OidcConfig(ISSUER, AUDIENCE, ISSUER + '/.well-known/jwks.json', **config), fetch=fetch)


def identify(v, value):
    return asyncio.run(v.identify(value))


def test_scopes_map_to_the_existing_roles_with_rsa_and_ec_keys():
    v = verifier()
    assert identify(v, token('sentinelops/incidents.write openid')) == ('producer', 'svc-producer')
    assert identify(v, token('sentinelops/incidents.read', key=EC_KEY, kid='ec-1', alg='ES256')) == \
        ('operator', 'svc-producer')
    assert identify(v, token(None, scp=['sentinelops/incidents.read'])) == ('operator', 'svc-producer')
    with pytest.raises(ScopeMissing):
        identify(v, token('openid profile'))


def test_approval_is_a_separate_verified_scope_and_does_not_depend_on_role_priority():
    v = verifier()
    read, write, approve = v.config.operator_scope, v.config.producer_scope, v.config.approval_scope
    for scopes in (read, write, read+' '+write, approve):
        with pytest.raises(ScopeMissing):
            asyncio.run(v.identify(token(scopes), required_scope=approve))
    assert asyncio.run(v.identify(token(read+' '+approve), required_scope=approve)) == ('approver', 'svc-producer')
    both = token(read+' '+write+' '+approve)
    assert asyncio.run(v.identify(both, required_scope=read))[0] == 'operator'
    assert asyncio.run(v.identify(both, required_scope=approve))[0] == 'approver'
    assert identify(v, both)[0] == 'producer'  # Existing producer behavior stays compatible.


def test_public_login_configuration_and_callback_only_expose_public_oidc_values():
    config = OidcConfig(ISSUER, AUDIENCE, ISSUER+'/.well-known/jwks.json', audience_claim='client_id',
                        login_domain='https://login.example')
    with TestClient(create_app(Settings(environment='aws-dev', oidc=config,
        control_database_url='postgresql://PRIVATE-DATABASE-CREDENTIAL', runbook_proposal_store='/PRIVATE-STORE'))) as api:
        result = api.get('/ui/auth-config')
        assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
        assert result.json() == {'mode': 'oidc', 'issuer': ISSUER, 'client_id': AUDIENCE,
            'domain': 'https://login.example', 'jwks_url': config.jwks_url,
            'scopes': ['openid', config.operator_scope, config.approval_scope],
            'read_scope': config.operator_scope, 'approval_scope': config.approval_scope, 'callback_path': '/ui/callback'}
        assert 'PRIVATE' not in result.text
        page = api.get('/ui/callback?code=PRIVATE-CODE&state=PRIVATE-STATE')
        assert page.status_code == 200 and 'text/html' in page.headers['content-type']
        assert 'PRIVATE' not in page.text
        policy = page.headers['content-security-policy']
        assert "connect-src 'self' https://login.example https://idp.example" in policy
        assert 'unsafe-inline' not in policy and "frame-ancestors 'none'" in policy
        assert api.head('/ui/callback').status_code == 200
        assert '/ui/callback' not in api.get('/openapi.json').json()['paths']
    for settings, mode in [(Settings(), 'manual'), (Settings(environment='aws'), 'unavailable')]:
        with TestClient(create_app(settings)) as api:
            assert api.get('/ui/auth-config').json() == {'mode': mode}


@pytest.mark.parametrize('change', [{'approval_scope':'sentinelops/incidents.read'},
    {'login_domain':'http://login.example'}, {'login_domain':'https://login.example/'},
    {'login_domain':'https://login.example?secret=x'}, {'login_domain':'https://u:p@login.example'},
    {'login_domain':'https://login.example:443'}, {'audience_claim':'aud'}, {'jwks_url':'https://other.example/keys'}])
def test_hosted_login_configuration_rejects_unsafe_or_mismatched_binding(change):
    base = dict(issuer=ISSUER,audience=AUDIENCE,jwks_url=ISSUER+'/.well-known/jwks.json',
                audience_claim='client_id', login_domain='https://login.example')
    with pytest.raises(ValueError): OidcConfig(**{**base,**change})


@pytest.mark.parametrize('case', ['expired', 'issuer', 'audience', 'foreign-key', 'unknown-kid', 'hs256',
                                  'none', 'id-token', 'no-sub', 'bad-sub', 'garbage', 'alg-mismatch'])
def test_invalid_tokens_are_rejected(case):
    v = verifier()
    now = int(time.time())
    value = {
        'expired': lambda: token(iat=now - 900, exp=now - 600),
        'issuer': lambda: token(iss='https://evil.example'),
        'audience': lambda: token(aud='another-api'),
        'foreign-key': lambda: token(key=OTHER_KEY),
        'unknown-kid': lambda: token(kid='rsa-9'),
        'hs256': lambda: jwt.encode({'iss': ISSUER, 'aud': AUDIENCE, 'sub': 's', 'iat': now, 'exp': now + 60,
                                     'scope': 'sentinelops/incidents.write'}, 'k' * 32, algorithm='HS256',
                                    headers={'kid': 'rsa-1'}),
        'none': lambda: jwt.encode({'iss': ISSUER, 'sub': 's'}, None, algorithm='none', headers={'kid': 'rsa-1'}),
        'id-token': lambda: token(token_use='id'),
        'no-sub': lambda: token(sub=None),
        'bad-sub': lambda: token(sub='bad subject with spaces'),
        'garbage': lambda: 'not-a-jwt',
        'alg-mismatch': lambda: token(key=EC_KEY, kid='rsa-1', alg='ES256'),
    }[case]()
    with pytest.raises(TokenRejected):
        identify(v, value)


def test_cognito_style_client_id_audience_only_for_unbound_access_tokens():
    v = verifier(audience_claim='client_id')
    write = 'sentinelops/incidents.write'
    assert identify(v, token(write, aud=None, client_id=AUDIENCE, token_use='access'))[0] == 'producer'
    for claims in ({'client_id': 'other-client', 'token_use': 'access'},
                   {'client_id': AUDIENCE},  # token_use must be explicit
                   {'client_id': AUDIENCE, 'token_use': 'id'}):
        with pytest.raises(TokenRejected):
            identify(v, token(write, aud=None, **claims))
    with pytest.raises(TokenRejected):  # bound to another resource server
        identify(v, token(write, aud='https://different-resource.example', client_id=AUDIENCE, token_use='access'))


@pytest.mark.parametrize('claims', [{'scope': None, 'scp': [['sentinelops/incidents.write']]},
                                    {'scope': {'a': 1}}, {'iat': {'nested': 1}}, {'exp': 'soon'}])
def test_claims_of_the_wrong_shape_are_rejected_not_raised(claims):
    claims = {'scope': 'sentinelops/incidents.write', **claims}
    with pytest.raises(TokenRejected):
        identify(verifier(), token(**claims))


def test_jwks_is_cached_refreshed_on_rotation_and_fails_closed_when_unreachable():
    calls = []
    v = verifier(calls=calls)
    identify(v, token())
    identify(v, token())
    assert len(calls) == 1
    with pytest.raises(TokenRejected):  # an unknown kid forces one refresh, then is rate limited
        identify(v, token(kid='rsa-9'))
    with pytest.raises(TokenRejected):
        identify(v, token(kid='rsa-9'))
    assert len(calls) == 2
    with pytest.raises(VerifierUnavailable):
        identify(verifier(ConnectionError('down')), token())
    with pytest.raises(VerifierUnavailable):
        identify(verifier({'keys': [{'kty': 'oct', 'k': 'c2VjcmV0', 'kid': 'h'}]}), token())


@pytest.mark.parametrize('bad', [{'issuer': 'http://idp.example'}, {'jwks_url': 'https://u:p@idp.example/k'},
                                 {'audience': ' '}, {'producer_scope': 'same', 'operator_scope': 'same'},
                                 {'audience_claim': 'azp'}, {'leeway_seconds': 3600}])
def test_configuration_is_validated(bad):
    base = {'issuer': ISSUER, 'audience': AUDIENCE, 'jwks_url': ISSUER + '/jwks'}
    with pytest.raises(ValueError):
        OidcConfig(**{**base, **bad})


def test_environment_configuration(monkeypatch):
    assert oidc_from_env() is None
    monkeypatch.setenv('SENTINELOPS_OIDC_ISSUER', ISSUER)
    with pytest.raises(ValueError, match='SENTINELOPS_OIDC_AUDIENCE'):
        oidc_from_env()
    monkeypatch.setenv('SENTINELOPS_OIDC_AUDIENCE', AUDIENCE)
    monkeypatch.setenv('SENTINELOPS_OIDC_JWKS_URL', ISSUER + '/jwks')
    monkeypatch.setenv('SENTINELOPS_OIDC_AUDIENCE_CLAIM', 'client_id')
    assert oidc_from_env().audience_claim == 'client_id'
    with pytest.raises(ValueError, match='either OIDC or static'):
        Settings(environment='aws', oidc=oidc_from_env(), api_token='x' * 32)


class Repository:
    def __init__(self):
        self.actors = []

    async def check_ready(self):
        pass


def test_api_accepts_oidc_outside_local_and_keeps_producer_and_operator_apart():
    app = create_app(Settings(environment='aws', oidc=verifier().config), repository=Repository(),
                     identity_verifier=verifier())
    with TestClient(app) as api:
        def get(value):
            return api.get('/ready/incidents', headers={'Authorization': 'Bearer ' + value})
        assert get(token('sentinelops/incidents.read')).status_code == 200
        assert get(token('sentinelops/incidents.write')).status_code == 200
        assert get(token('openid')).json()['code'] == 'insufficient_scope'
        assert get(token(aud='other')).status_code == 401
        assert api.get('/ready/incidents').status_code == 401
        sample = json.loads(open('samples/incident-evidence-record.sample.json').read())
        write = api.post('/v1/incidents', json=sample,
                         headers={'Authorization': 'Bearer ' + token('sentinelops/incidents.read'),
                                  'Idempotency-Key': 'k1', 'If-Match': '"0"'})
        assert write.status_code == 403 and write.json()['code'] == 'read_only_credential'
        assert api.get('/ready').json()['dependencies']['authentication'] is True


def test_provider_outage_backs_off_and_stale_keys_expire():
    clock, calls, state = [0.], [], {'up': True}

    async def fetch(url):
        calls.append(clock[0])
        if not state['up']:
            raise ConnectionError('down')
        return JWKS
    v = OidcVerifier(OidcConfig(ISSUER, AUDIENCE, ISSUER + '/jwks', jwks_ttl_seconds=60), fetch=fetch,
                     clock=lambda: clock[0])
    identify(v, token())
    state['up'], clock[0] = False, 61.
    for _ in range(5):  # cache expired, provider down: one fetch, then back off, old keys still verify
        assert identify(v, token())[0] == 'operator'
    assert len(calls) == 2
    clock[0] = 241.  # past 4 cache lifetimes without a successful refresh
    with pytest.raises(VerifierUnavailable):
        identify(v, token())


def _time_claim_cases():
    now = int(time.time())
    return [{'exp': str(now + 300)}, {'iat': str(now)}, {'nbf': str(now - 10)},
            {'exp': float('inf')}, {'exp': float('-inf')}, {'iat': float('inf')}, {'nbf': float('nan')},
            {'exp': True}, {'iat': [now]}, {'exp': 10 ** 400}, {'exp': 1e300}, {'iat': -1}]


@pytest.mark.parametrize('claims', _time_claim_cases(),
                         ids=['exp-str', 'iat-str', 'nbf-str', 'exp-inf', 'exp-neg-inf', 'iat-inf', 'nbf-nan',
                              'exp-bool', 'iat-list', 'exp-huge-int', 'exp-huge-float', 'iat-negative'])
def test_time_claims_must_be_finite_numeric_dates(claims):
    # Codex review 2026-11-10: PyJWT coerces numeric strings with int() and Infinity overflowed into a 500.
    with pytest.raises(TokenRejected):
        identify(verifier(), token('sentinelops/incidents.write', **claims))


def test_api_answers_401_not_500_for_invalid_time_claims():
    app = create_app(Settings(environment='aws', oidc=verifier().config), repository=Repository(),
                     identity_verifier=verifier())
    with TestClient(app, raise_server_exceptions=False) as api:
        for claims in _time_claim_cases():
            answer = api.get('/ready/incidents',
                             headers={'Authorization': 'Bearer ' + token('sentinelops/incidents.read', **claims)})
            assert answer.status_code == 401, claims
        assert api.get('/ready/incidents', headers={
            'Authorization': 'Bearer ' + token('sentinelops/incidents.read', nbf=int(time.time()) - 10)}).status_code == 200
