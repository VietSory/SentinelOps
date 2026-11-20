"""Keep native HTTP acceptance aligned with the actual explanation contract."""
from copy import deepcopy

import pytest

from scripts.check_native_incident_api import assert_read_only_explanation
from tests.test_explanation_api import HEADERS, ReadOnlyRepository, client, path


def actual_fallback():
    repository = ReadOnlyRepository()
    with client(repository) as api:
        response = api.get(path(repository) + '?revision=1', headers=HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert result['fallback'] and result['provider'] == 'deterministic-facts/v1'
    return result['payload']


def test_native_acceptance_accepts_the_actual_v2_http_fallback():
    assert_read_only_explanation(actual_fallback())


@pytest.mark.parametrize('replacement', ['', 'Phần giải thích này cho phép hành động.'])
def test_native_acceptance_still_rejects_missing_or_inverted_read_only_disclaimer(replacement):
    payload = deepcopy(actual_fallback())
    payload['summary'] = payload['summary'].replace(
        'Phần giải thích này không cho phép hành động.', replacement)
    with pytest.raises(AssertionError):
        assert_read_only_explanation(payload)
