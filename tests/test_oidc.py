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


def test_cognito_style_client_id_audience():
    v = verifier(audience_claim='client_id')
    assert identify(v, token('sentinelops/incidents.write', aud=None, client_id=AUDIENCE))[0] == 'producer'
    with pytest.raises(TokenRejected):
        identify(v, token('sentinelops/incidents.write', aud=None, client_id='other-client'))


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
