"""Render offline durable PVCs for the EKS health and incident workers.

The output references no cluster, credentials, or cloud API. Apply these PVCs
before the worker manifests. Optional model and deployment-change stores are
emitted only when their claim names are explicitly selected.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

import yaml

from scripts.render_business_manifests import _label
from scripts.render_worker_manifests import NAMESPACE

DEFAULT_STORAGE_CLASS = 'sentinelops-gp3-kms'
HEALTH_STORE_CLAIM = 'health-artifacts'
INCIDENT_STORE_CLAIM = 'incident-artifacts'
MAX_SIZE_GIB = 64
SIZE = re.compile(r'([1-9][0-9]*)Gi')


def _size(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(name + ' must be a whole number of Gi')
    match = SIZE.fullmatch(value)
    if match is None or int(match.group(1)) > MAX_SIZE_GIB:
        raise ValueError(name + f' must be a whole number of Gi from 1Gi to {MAX_SIZE_GIB}Gi')
    return value


def render(*, storage_class: str = DEFAULT_STORAGE_CLASS,
           health_store_claim: str = HEALTH_STORE_CLAIM,
           incident_store_claim: str = INCIDENT_STORE_CLAIM,
           model_claim: str | None = None,
           deployment_change_claim: str | None = None,
           health_size: str = '5Gi', incident_size: str = '5Gi',
           model_size: str = '2Gi', deployment_change_size: str = '1Gi') -> list[dict]:
    """Return bounded ReadWriteOnce PVC definitions without contacting Kubernetes."""
    _label(storage_class, 'storage class')
    claims = [(health_store_claim, health_size, 'health artifact claim'),
              (incident_store_claim, incident_size, 'incident artifact claim')]
    if model_claim is not None:
        claims.append((model_claim, model_size, 'model claim'))
    if deployment_change_claim is not None:
        claims.append((deployment_change_claim, deployment_change_size, 'deployment change claim'))
    names = []
    for claim, size, name in claims:
        _label(claim, name)
        _size(size, name + ' size')
        names.append(claim)
    if len(set(names)) != len(names):
        raise ValueError('health, incident, model and deployment change stores require separate PVCs')
    labels = {'app.kubernetes.io/part-of': 'sentinelops'}
    return [{'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim',
             'metadata': {'name': claim, 'namespace': NAMESPACE, 'labels': dict(labels)},
             'spec': {'accessModes': ['ReadWriteOnce'], 'storageClassName': storage_class,
                      'resources': {'requests': {'storage': size}}}}
            for claim, size, _ in claims]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage-class', default=DEFAULT_STORAGE_CLASS)
    parser.add_argument('--health-store-claim', default=HEALTH_STORE_CLAIM)
    parser.add_argument('--incident-store-claim', default=INCIDENT_STORE_CLAIM)
    parser.add_argument('--model-claim', help='Optional claim mounted read-only by the incident worker')
    parser.add_argument('--deployment-change-claim',
                        help='Optional immutable deployment-change claim mounted read-only by the incident worker')
    parser.add_argument('--health-size', default='5Gi')
    parser.add_argument('--incident-size', default='5Gi')
    parser.add_argument('--model-size', default='2Gi')
    parser.add_argument('--deployment-change-size', default='1Gi')
    parser.add_argument('--output', type=Path, required=True)
    args = vars(parser.parse_args())
    output = args.pop('output')
    documents = render(**args)
    with output.open('x') as stream:
        yaml.safe_dump_all(documents, stream, sort_keys=False)


if __name__ == '__main__':
    main()
