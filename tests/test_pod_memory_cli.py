"""Credential fallback is bounded to the verified control database, in RAM."""
import pytest

from scripts.run_pod_memory_demo import database_url


BASE = 'postgresql://reader@db.internal:5432/sentinelops_control?sslmode=verify-full'


def test_secret_refs_build_writer_url_in_memory_and_remove_source_credentials():
    env = {'CONTROL_DATABASE_URL': BASE, 'UI_DATABASE_PASSWORD': 'contains:@/?'}
    assert database_url(env) == 'postgresql://control_writer:contains%3A%40%2F%3F@db.internal:5432/sentinelops_control?sslmode=verify-full'
    assert env == {}  # No credential-bearing URL written into the environment.


@pytest.mark.parametrize('base', [BASE.replace('verify-full', 'require'),
    BASE.replace('sentinelops_control', 'commerce'), BASE.replace('postgresql', 'http'),
    BASE + '#fragment', BASE.split('?')[0], ''])
def test_secret_fallback_rejects_unverified_or_wrong_database_before_connect(base):
    env = {'CONTROL_DATABASE_URL': base, 'UI_DATABASE_PASSWORD': 'do-not-print'}
    with pytest.raises(ValueError, match='verified control database Secret configuration is required'):
        database_url(env)
    assert env == {}


def test_explicit_runtime_database_keeps_existing_precedence_and_tls_guard():
    explicit = BASE.replace('reader@', 'control_writer:encoded@')
    assert database_url({'SENTINELOPS_DATABASE_URL': explicit}) == explicit
    with pytest.raises(ValueError):
        database_url({'SENTINELOPS_DATABASE_URL': explicit.replace('verify-full', 'disable')})
    assert database_url({'SENTINELOPS_DATABASE_URL': 'postgresql://writer@127.0.0.1/sentinelops_control'})
