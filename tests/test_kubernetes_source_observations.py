"""Software-only negatives; accepted source/H4 authority is never derived here."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from scripts.check_kubernetes_testbed import pod_samples, trace_identities
from scripts.kubernetes_testbed import NAMESPACE, SERVICES


def fixture():
    inventory = {s: {'pod_uid': 'uid-' + s, 'pod_name': 'pod-' + s} for s in SERVICES}
    resources = []
    for service, row in inventory.items():
        attrs = {'service.name': service, 'k8s.pod.uid': row['pod_uid'],
                 'service.instance.id': row['pod_uid'], 'k8s.pod.name': row['pod_name'],
                 'k8s.namespace.name': NAMESPACE}
        resources.append({'resource': {'attributes': [{'key': k, 'value': {'stringValue': v}} for k, v in attrs.items()]}})
    return inventory, {'result': {'resourceSpans': resources}}


def test_resource_identity_requires_every_service_and_exact_native_pod_metadata():
    inventory, body = fixture()
    assert trace_identities(json.dumps(body), inventory) == {s: row['pod_uid'] for s, row in inventory.items()}
    for key in ('k8s.pod.uid', 'service.instance.id', 'k8s.namespace.name', 'k8s.pod.name'):
        corrupted = deepcopy(body)
        fields = corrupted['result']['resourceSpans'][0]['resource']['attributes']
        next(v for v in fields if v['key'] == key)['value']['stringValue'] = 'foreign'
        with pytest.raises(ValueError): trace_identities(json.dumps(corrupted), inventory)
    body['result']['resourceSpans'].pop()
    with pytest.raises(ValueError, match='incomplete'): trace_identities(json.dumps(body), inventory)


@pytest.mark.parametrize('problem', ['missing-uid', 'foreign-uid', 'namespace', 'zero', 'nan', 'future', 'ambiguous'])
def test_ambiguous_missing_or_invalid_business_metric_identity_is_not_a_binding(problem):
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    body = {'status': 'success', 'data': {'resultType': 'vector', 'result': [{
        'metric': {'k8s_pod_uid': 'expected', 'k8s_namespace_name': NAMESPACE}, 'value': [now.timestamp(), '60']}]}}
    assert pod_samples(body, {'pod_uid': 'expected'}, as_of=now) == 60
    row = body['data']['result'][0]
    if problem == 'missing-uid': row['metric'].pop('k8s_pod_uid')
    if problem == 'foreign-uid': row['metric']['k8s_pod_uid'] = 'other'
    if problem == 'namespace': row['metric']['k8s_namespace_name'] = 'default'
    if problem == 'zero': row['value'][1] = '0'
    if problem == 'nan': row['value'][1] = 'nan'
    if problem == 'future': row['value'][0] += 1
    if problem == 'ambiguous': body['data']['result'].append(deepcopy(row))
    with pytest.raises(ValueError): pod_samples(body, {'pod_uid': 'expected'}, as_of=now)
