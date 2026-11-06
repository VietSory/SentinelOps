import httpx
import pytest

from sentinelops.public_data import gamma


def test_temporary_publisher_failure_retries_exact_request(monkeypatch):
    calls, delays = [], []
    monkeypatch.setattr(gamma.time, 'sleep', delays.append)
    def serve(request):
        calls.append(str(request.url))
        return httpx.Response(503 if len(calls) == 1 else 200, json={'source': 'unchanged-v1'})
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        assert gamma._json(client, 'https://publisher.invalid/view/frozen', params={'version': 1}) == {'source': 'unchanged-v1'}
    assert len(calls) == 2 and len(set(calls)) == 1 and delays == [1]


@pytest.mark.parametrize('kind', ['503', 'transport'])
def test_transient_errors_stop_after_three_attempts(monkeypatch, kind):
    calls, delays = [], []
    monkeypatch.setattr(gamma.time, 'sleep', delays.append)
    def serve(request):
        calls.append(request)
        if kind == 'transport': raise httpx.ConnectError('fixture', request=request)
        return httpx.Response(503)
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        with pytest.raises(httpx.HTTPError): gamma._json(client, 'https://publisher.invalid/view/frozen')
    assert len(calls) == 3 and delays == [1, 2]


@pytest.mark.parametrize('kind', ['401', '403', '404', 'invalid-json', 'oversize'])
def test_permanent_or_integrity_failure_is_never_retried(monkeypatch, kind):
    calls = []
    monkeypatch.setattr(gamma.time, 'sleep', lambda *_: pytest.fail('unsafe retry'))
    def serve(request):
        calls.append(request)
        return httpx.Response(int(kind), json={}) if kind.isdigit() else httpx.Response(
            200, content=b'not-json' if kind == 'invalid-json' else b'{"large":"payload"}')
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        with pytest.raises((ValueError, httpx.HTTPError)):
            gamma._json(client, 'https://publisher.invalid/view/frozen', budget=12)
    assert len(calls) == 1
