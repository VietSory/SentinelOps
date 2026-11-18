"""Offline PVC renderer checks; no Kubernetes or cloud calls."""
from __future__ import annotations

import sys

import pytest
import yaml

from scripts import render_worker_storage as storage


def test_default_worker_store_claims_are_explicit_labeled_rwo_pvcs():
    documents = storage.render()
    for document, name, size in zip(documents, (storage.HEALTH_STORE_CLAIM,
                                                 storage.INCIDENT_STORE_CLAIM),
                                    ('5Gi', '5Gi'), strict=True):
        assert document == {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': {
            'name': name, 'namespace': 'sentinelops-b-actions',
            'labels': {'app.kubernetes.io/part-of': 'sentinelops'}}, 'spec': {
            'accessModes': ['ReadWriteOnce'], 'storageClassName': storage.DEFAULT_STORAGE_CLASS,
            'resources': {'requests': {'storage': size}}}}


def test_optional_worker_store_claims_and_sizes_are_rendered():
    documents = storage.render(model_claim='models', deployment_change_claim='deployment-changes',
                               model_size='3Gi', deployment_change_size='2Gi')
    assert [(d['metadata']['name'], d['spec']['resources']['requests']['storage']) for d in documents] == [
        ('health-artifacts', '5Gi'), ('incident-artifacts', '5Gi'), ('models', '3Gi'),
        ('deployment-changes', '2Gi')]


@pytest.mark.parametrize('options', [
    {'storage_class': 'invalid_class'}, {'health_store_claim': 'not/a-claim'},
    {'health_size': '0Gi'}, {'incident_size': '65Gi'}, {'model_claim': 'models', 'model_size': '512Mi'},
    {'deployment_change_claim': 'deployment-changes', 'deployment_change_size': '65Gi'},
    {'model_claim': 'health-artifacts'}, {'deployment_change_claim': 'incident-artifacts'},
])
def test_worker_store_validation_fails_closed(options):
    with pytest.raises(ValueError):
        storage.render(**options)


def test_cli_is_deterministic_and_refuses_to_overwrite(tmp_path, monkeypatch):
    first, second = tmp_path / 'first.yaml', tmp_path / 'second.yaml'
    for output in (first, second):
        monkeypatch.setattr(sys, 'argv', ['render_worker_storage', '--model-claim', 'models',
            '--deployment-change-claim', 'deployment-changes', '--output', str(output)])
        storage.main()
    assert first.read_bytes() == second.read_bytes()
    assert list(yaml.safe_load_all(first.read_text())) == storage.render(
        model_claim='models', deployment_change_claim='deployment-changes')
    with pytest.raises(FileExistsError):
        storage.main()
