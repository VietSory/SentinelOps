"""Identity attributes remain observations, never H4 or source authentication."""
import json

import pytest

from services.common.observability import kubernetes_identity
from services.common import runtime

ENV = {'SENTINELOPS_K8S_POD_UID': '66b24412-2ce9-48bd-a8ba-f4eb9facb5ec',
       'SENTINELOPS_K8S_POD_NAME': 'payment-service-6d5949db9-x8f7t',
       'SENTINELOPS_K8S_NAMESPACE': 'sentinelops-b-actions'}


def test_compose_has_no_invented_pod_and_complete_downward_metadata_is_preserved():
    assert kubernetes_identity({}) == {}
    result = kubernetes_identity(ENV)
    assert result['k8s.pod.uid'] == ENV['SENTINELOPS_K8S_POD_UID']
    assert result['k8s.namespace.name'] == 'sentinelops-b-actions'


@pytest.mark.parametrize('key,value', [('SENTINELOPS_K8S_POD_UID', ''),
    ('SENTINELOPS_K8S_POD_UID', 'invented-pod'), ('SENTINELOPS_K8S_NAMESPACE', 'namespace\nforged'),
    ('SENTINELOPS_K8S_POD_NAME', '<script>')])
def test_missing_partial_or_malformed_metadata_fails_startup(key, value):
    with pytest.raises(ValueError): kubernetes_identity({key: value})
    with pytest.raises(ValueError): kubernetes_identity({**ENV, key: value})


def test_logs_include_same_uid_without_accepting_it_from_request_headers(monkeypatch, caplog):
    for key, value in ENV.items(): monkeypatch.setenv(key, value)
    with caplog.at_level('INFO', logger='sentinelops-testbed'):
        runtime.log_event('payment-service', 'test_identity')
    result = json.loads(caplog.records[-1].message)
    assert result['k8s.pod.uid'] == ENV['SENTINELOPS_K8S_POD_UID']
    assert 'dispatch_authorized' not in result
