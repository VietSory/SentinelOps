"""Explicit local HTTPS Kubernetes adapter, with no ambient kubeconfig or AWS SDK.

Only a named Payment Deployment can be patched; the caller still needs the
executor's policy, authority, approved ledger and committed dispatch journal.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import ssl
from urllib.parse import urlsplit

import httpx

from sentinelops.remediation.domain import (
    Blocked, Deployment, KubernetesUnavailable, Pod, Target, rollback_patch,
)
from sentinelops.telemetry.core import timestamp


def local_server(endpoint):
    parsed = urlsplit(endpoint)
    if (parsed.scheme != 'https' or parsed.hostname != '127.0.0.1' or not parsed.port
        or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError('rollback adapter requires an explicit credential-free local HTTPS API server')
    return endpoint.rstrip('/')


def _bound(value, replicas, *, surge=False):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r'(?:[0-9]|[1-9][0-9]|100)%', value):
        count = replicas * int(value[:-1]) / 100
        return math.ceil(count) if surge else math.floor(count)
    raise Blocked('invalid-rollout-bound')


def _owner(body, uid, kind):
    return any(item.get('uid') == uid and item.get('kind') == kind and item.get('controller') is True
               for item in body.get('metadata', {}).get('ownerReferences', []))


class LocalKubernetes:
    def __init__(self, endpoint: str, target: Target, *, token_file: Path, ca_file: Path,
                 transport=None, clock=lambda: datetime.now(timezone.utc)):
        endpoint = local_server(endpoint)
        metadata = token_file.lstat()
        if (not token_file.is_file() or token_file.is_symlink() or metadata.st_mode & 0o077
            or metadata.st_uid != os.getuid()):
            raise ValueError('Kubernetes token must be an owned private regular file')
        token = token_file.read_text().strip()
        if len(token) < 32 or re.search(r'\s', token):
            raise ValueError('invalid Kubernetes bearer token')
        tls = ssl.create_default_context(cafile=str(ca_file))
        self.target, self.clock = target, clock
        self.client = httpx.AsyncClient(base_url=endpoint, verify=tls, transport=transport,
            headers={'Authorization': 'Bearer ' + token}, timeout=5, follow_redirects=False, trust_env=False)

    async def close(self):
        await self.client.aclose()

    async def _request(self, method, path, **kwargs):
        try:
            response = await self.client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise KubernetesUnavailable('kubernetes-transport-unavailable') from None
        if response.status_code in (401, 403):
            raise Blocked('kubernetes-rbac-denied')
        if response.status_code in (404, 409, 422):
            raise Blocked('kubernetes-target-missing-or-precondition-failed')
        if response.status_code != 200 or len(response.content) > 2_000_000:
            raise KubernetesUnavailable('kubernetes-response-unavailable')
        try:
            result = response.json()
            if not isinstance(result, dict): raise ValueError()
            return result
        except (ValueError, TypeError):
            raise KubernetesUnavailable('kubernetes-response-invalid') from None

    @property
    def path(self):
        return '/apis/apps/v1/namespaces/' + self.target.namespace + '/deployments/' + self.target.deployment

    def _deployment(self, body):
        meta = body.get('metadata', {})
        if (body.get('kind') != 'Deployment' or body.get('apiVersion') != 'apps/v1'
            or meta.get('uid') != self.target.deployment_uid or meta.get('name') != self.target.deployment
            or meta.get('namespace') != self.target.namespace or not isinstance(meta.get('resourceVersion'), str)):
            raise Blocked('deployment-identity-mismatch')
        spec, state = body['spec'], body.get('status', {})
        containers = spec['template']['spec']['containers']
        matching = [(i, c) for i, c in enumerate(containers) if c.get('name') == self.target.container]
        if len(matching) != 1:
            raise Blocked('payment-container-identity-mismatch')
        index, container = matching[0]
        replicas = spec.get('replicas', 1)
        if type(replicas) is not int or replicas < 0: raise Blocked('invalid-replica-count')
        strategy = spec.get('strategy', {})
        rolling = strategy.get('rollingUpdate', {})
        return dict(target=self.target, resource_version=meta['resourceVersion'], generation=meta['generation'],
            observed_generation=state.get('observedGeneration', 0), image=container['image'], container_index=index,
            replicas=replicas, updated_replicas=state.get('updatedReplicas', 0), ready_replicas=state.get('readyReplicas', 0),
            available_replicas=state.get('availableReplicas', 0),
            max_surge=_bound(rolling.get('maxSurge', '25%'), replicas, surge=True),
            max_unavailable=_bound(rolling.get('maxUnavailable', '25%'), replicas),
            strategy=strategy.get('type', 'RollingUpdate'), paused=spec.get('paused', False))

    async def observe(self):
        cluster, namespace = await asyncio.gather(self._request('GET', '/api/v1/namespaces/kube-system'),
            self._request('GET', '/api/v1/namespaces/' + self.target.namespace))
        if (cluster.get('metadata', {}).get('uid') != self.target.cluster_uid
            or namespace.get('metadata', {}).get('uid') != self.target.namespace_uid
            or namespace['metadata'].get('labels', {}).get('sentinelops.dev/lane') != 'B'):
            raise Blocked('cluster-or-namespace-identity-mismatch')
        body = await self._request('GET', self.path)
        initial = self._deployment(body)
        selector = body['spec'].get('selector', {})
        labels = selector.get('matchLabels', {})
        if (set(selector) != {'matchLabels'} or not 1 <= len(labels) <= 10
            or any(not re.fullmatch(r'[a-zA-Z0-9./_-]+', key) or not isinstance(v, str)
                   or not re.fullmatch(r'[a-zA-Z0-9._-]+', v) for key, v in labels.items())):
            raise Blocked('unsupported-payment-selector')
        params = {'labelSelector': ','.join(k + '=' + v for k, v in sorted(labels.items())), 'limit': '100'}
        base = '/apis/apps/v1/namespaces/' + self.target.namespace
        sets, pods, hpas = await asyncio.gather(
            self._request('GET', base + '/replicasets', params=params),
            self._request('GET', '/api/v1/namespaces/' + self.target.namespace + '/pods', params=params),
            self._request('GET', '/apis/autoscaling/v2/namespaces/' + self.target.namespace + '/horizontalpodautoscalers', params={'limit':'100'}))
        for collection in (sets, pods, hpas):
            if (not isinstance(collection.get('items'), list) or len(collection['items']) > 100
                or collection.get('metadata', {}).get('continue')):
                raise Blocked('incomplete-kubernetes-inventory')
        owned = {s['metadata']['uid'] for s in sets['items'] if _owner(s, self.target.deployment_uid, 'Deployment')}
        states = []
        for pod in pods['items']:
            if not any(_owner(pod, uid, 'ReplicaSet') for uid in owned):
                raise Blocked('pod-does-not-belong-to-payment-deployment')
            matching = [c for c in pod['spec']['containers'] if c.get('name') == self.target.container]
            statuses = [c for c in pod.get('status', {}).get('containerStatuses', []) if c.get('name') == self.target.container]
            if len(matching) != 1 or len(statuses) > 1: raise Blocked('invalid-payment-pod-container')
            status = statuses[0] if statuses else {}
            states.append(Pod(pod['metadata']['uid'], matching[0]['image'],
                status.get('ready') is True and 'running' in status.get('state', {})
                and not pod['metadata'].get('deletionTimestamp'), status.get('imageID', '')))
        hpa = any(h.get('spec', {}).get('scaleTargetRef', {}).get('kind') == 'Deployment'
            and h['spec']['scaleTargetRef'].get('name') == self.target.deployment for h in hpas['items'])
        final = self._deployment(await self._request('GET', self.path))
        if final['resource_version'] != initial['resource_version']:
            raise KubernetesUnavailable('deployment-changed-during-inventory-retry-snapshot')
        return Deployment(**final, hpa=hpa, pods=tuple(sorted(states, key=lambda p:p.uid)),
                          observed_at=timestamp(self.clock()))

    async def patch(self, before: Deployment, image: str, *, dry_run: bool):
        if before.target != self.target or type(dry_run) is not bool:
            raise Blocked('patch-target-or-dry-run-mismatch')
        body = await self._request('PATCH', self.path,
            headers={'Content-Type': 'application/json-patch+json'},
            content=json.dumps(rollback_patch(before, image), allow_nan=False),
            params={'dryRun':'All'} if dry_run else {})
        after = self._deployment(body)
        if after['image'] != image or after['replicas'] != before.replicas:
            raise Blocked('patch-response-image-or-replicas-mismatch')
        return {**after, 'target': self.target.key, 'observed_at': timestamp(self.clock())}
