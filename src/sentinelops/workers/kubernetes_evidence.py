"""Read-only Kubernetes incident evidence bound to accepted local HTTP counters.

Pod identity is observed from the API and instrumented resources, never inferred
from a service name. A capture is observational; model/action approvals are separate.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import json
import math
from urllib.parse import urlencode

import httpx
import yaml

from sentinelops.contracts import utc_time
from sentinelops.incidents.rca_source import reject_control
from sentinelops.incidents.rca import validate_policy
from sentinelops.remediation.domain import Target, KubernetesUnavailable, image_digest
from sentinelops.remediation.kubernetes import _owner
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.prometheus import REQUESTS, matches_source_configuration, decode_capture
from sentinelops.workers.incident_evidence import (
    LOG_BYTES, LOG_FIELDS, TRACE_BYTES, NativeIncidentEvidence, completion_records, now,
)

K8S_FIELDS = {'k8s.pod.uid', 'k8s.pod.name', 'k8s.namespace.name'}


def project_inventory(deployments, replicasets, pods, *, target, deployment_uids):
    """Project bounded API observations; verify every Deployment → RS → Pod link."""
    result = {}
    if set(deployments) != set(BUSINESS_ROUTES) or set(deployment_uids) != set(BUSINESS_ROUTES):
        raise ValueError('Kubernetes business inventory must pin all three Deployments')
    for service, deployment in deployments.items():
        meta, spec = deployment['metadata'], deployment['spec']
        if (meta['name'] != service or meta['namespace'] != target.namespace
            or meta['uid'] != deployment_uids[service] or spec.get('replicas') != 1
            or meta.get('deletionTimestamp') or type(meta['generation']) is not int
            or meta['generation'] < 1 or len(spec['template']['spec']['containers']) != 1):
            raise ValueError('Kubernetes business Deployment identity or replica scope changed')
        container = spec['template']['spec']['containers'][0]
        if container['name'] != service.removesuffix('-service'):
            raise ValueError('Kubernetes business container name mismatch')
        image_digest(container['image'])
        sets = [r for r in replicasets[service] if _owner(r, meta['uid'], 'Deployment')]
        children = [p for p in pods[service] if any(_owner(p, r['metadata']['uid'], 'ReplicaSet') for r in sets)]
        if len(children) != 1 or len(pods[service]) != 1:
            raise ValueError('Kubernetes business Pod ownership is ambiguous or unavailable')
        pod = children[0]; pm = pod['metadata']; statuses = pod.get('status', {}).get('containerStatuses', [])
        pc = pod['spec']['containers']
        if (pm['namespace'] != target.namespace or pm.get('deletionTimestamp')
            or len(statuses) != 1 or statuses[0].get('name') != container['name']
            or not statuses[0].get('state', {}).get('running') or not statuses[0].get('imageID')
            or len(pc) != 1 or pc[0]['name'] != container['name'] or pc[0]['image'] != container['image']):
            raise ValueError('Kubernetes business Pod/container is not the selected running image')
        values = [meta['uid'], pm['uid'], pm['name'], statuses[0]['imageID']]
        if any(not isinstance(v, str) or not 1 <= len(v) <= 1024 for v in values):
            raise ValueError('Kubernetes identity scalar exceeds supported bounds')
        result[service] = {'service': service, 'deployment_uid': meta['uid'], 'generation': meta['generation'],
            'pod_uid': pm['uid'], 'pod_name': pm['name'], 'namespace': pm['namespace'],
            'image': container['image'], 'image_id': statuses[0]['imageID'], 'running': True}
    if deployment_uids['payment-service'] != target.deployment_uid:
        raise ValueError('Kubernetes Payment target differs from pinned business inventory')
    return result


def pod_query(service):
    method, route = BUSINESS_ROUTES[service]
    selector = ','.join(k + '=' + json.dumps(v) for k, v in
                        [('service', service), ('method', method), ('route', route)])
    return 'sum by (k8s_pod_uid,k8s_namespace_name) (increase(' + REQUESTS + '{' + selector + '}[60s]))'


def verify_pod_counter(body, expected, raw):
    """Exact instant/UID and the entire business request denominator must match."""
    end = utc_time(raw['bucket_end'])
    decoded = decode_capture(raw, environment='local')
    if not decoded['quality']['eligible_for_inference']:
        raise ValueError('Kubernetes binding requires an eligible original HTTP bucket')
    if body.get('status') != 'success' or body.get('warnings') or body.get('data', {}).get('resultType') != 'vector':
        raise ValueError('Kubernetes per-Pod counter is unavailable')
    rows = body['data']['result']
    # The collector can keep a terminated Pod's counter series. Only positive
    # increases contribute requests, but every returned row must still be valid.
    active, seen = [], set()
    for row in rows:
        clock, value = row['value']
        labels = row['metric']
        uid = labels.get('k8s_pod_uid')
        count = float(value)
        if (not isinstance(uid, str) or not uid or uid in seen
                or labels != {'k8s_pod_uid': uid, 'k8s_namespace_name': expected['namespace']}
                or float(clock) != end.timestamp() or not math.isfinite(count) or count < 0):
            raise ValueError('Kubernetes Pod counter has invalid identity, clock or increase')
        seen.add(uid)
        if count > 0:
            active.append((uid, count))
    if len(active) != 1:
        raise ValueError('Kubernetes request denominator spans missing or multiple serving Pods')
    uid, count = active[0]
    original = raw['queries']['requests']['response']['data']['result']
    if (uid != expected['pod_uid'] or len(original) != 1
            or not math.isclose(count, float(original[0]['value'][1]), rel_tol=1e-8, abs_tol=1e-8)):
        raise ValueError('Kubernetes Pod counter differs from accepted HTTP source/clock/denominator')


def trace_pods(body, inventory):
    """Check every emitted business resource; native decoder checks span semantics."""
    if len(body.encode()) > TRACE_BYTES: raise ValueError('Kubernetes trace exceeds byte budget')
    found = set()
    for line in body.splitlines():
        if not line.strip(): continue
        frame = json.loads(line)
        reject_control(frame)
        if 'error' in frame: raise ValueError('Jaeger returned an error')
        for resource in frame['result'].get('resourceSpans', []):
            pairs = resource.get('resource', {}).get('attributes', [])
            attrs = {a['key']: a['value'].get('stringValue') for a in pairs}
            if len(attrs) != len(pairs): raise ValueError('duplicate trace resource attributes')
            service = attrs.get('service.name')
            if service not in inventory: raise ValueError('trace resource service outside Kubernetes inventory')
            pod = inventory[service]
            if (attrs.get('k8s.pod.uid') != pod['pod_uid'] or attrs.get('service.instance.id') != pod['pod_uid']
                or attrs.get('k8s.pod.name') != pod['pod_name'] or attrs.get('k8s.namespace.name') != pod['namespace']):
                raise ValueError('trace resource differs from observed Kubernetes Pod')
            found.add(service)
    if not found: raise ValueError('trace has no observed business Pod resource')
    return found


def kubernetes_completions(body, identity, lower, upper, received):
    """Keep original instrumented UID fields, then reuse native business validation."""
    if len(body) > LOG_BYTES: raise ValueError('Kubernetes log exceeds byte budget')
    selected = []
    for line in body.decode('utf-8').splitlines():
        try: row = json.loads(line)
        except ValueError: continue
        if not isinstance(row, dict) or row.get('event') != 'request_completed': continue
        if (row.get('method'), row.get('route')) != BUSINESS_ROUTES[identity['service']]: continue
        reject_control(row)
        if (set(row) - LOG_FIELDS - K8S_FIELDS
            or row.get('k8s.pod.uid') != identity['pod_uid']
            or row.get('k8s.pod.name') != identity['pod_name']
            or row.get('k8s.namespace.name') != identity['namespace']):
            raise ValueError('business completion differs from observed Kubernetes Pod')
        selected.append({k:v for k,v in row.items() if k != 'path'})
    projected = [{k:v for k,v in row.items() if k not in K8S_FIELDS} for row in selected]
    records, counts = completion_records(b'\n'.join(json.dumps(r).encode() for r in projected),
                                        identity['service'], lower, upper, received)
    if 'quarantined-service-log-capture' in counts or 'too-many-business-completions' in counts:
        raise ValueError('Kubernetes business completion projection is invalid')
    kept = {r['request_instance_id'] for r in records}
    return records, [r for r in selected if r['request_instance_id'] in kept], counts


class KubernetesIncidentEvidence(NativeIncidentEvidence):
    requires_source_state = True

    def __init__(self, kubernetes, deployment_uids, *, prometheus_endpoint, jaeger_endpoint):
        if (set(deployment_uids) != set(BUSINESS_ROUTES)
            or deployment_uids.get('payment-service') != kubernetes.target.deployment_uid
            or any(not isinstance(v,str) or not 1 <= len(v) <= 128 for v in deployment_uids.values())):
            raise ValueError('pin three Kubernetes Deployment UIDs')
        super().__init__('kubernetes-read-only', prometheus_endpoint=prometheus_endpoint, jaeger_endpoint=jaeger_endpoint)
        self.kube, self.deployment_uids = kubernetes, dict(deployment_uids)
        self.profile.update(profile_version='kubernetes-incident-evidence/v1',
            target=dict(vars(kubernetes.target)), deployment_uids=self.deployment_uids)
        self.profile.pop('compose_project')

    async def _http(self, path, *, limit):
        try:
            return await super()._http(path, limit=limit)
        except (httpx.HTTPError, TimeoutError):
            raise KubernetesUnavailable('kubernetes-telemetry-http-unavailable') from None

    async def close(self):
        try: await super().close()
        finally: await self.kube.close()

    async def verify_source(self, identity):
        if (identity['environment'] != 'local'
            or identity['endpoint_hash'] != stable_id({'endpoint': self.prometheus_endpoint})):
            raise ValueError('Kubernetes evidence endpoint differs from accepted health source')
        await self.kube.require_observer()
        # Namespace/cluster identity and the pinned Payment target are rechecked
        # through the authenticated API before reading any business logs.
        await self.kube.observe()
        prefix = '/apis/apps/v1/namespaces/' + self.kube.target.namespace
        core = '/api/v1/namespaces/' + self.kube.target.namespace
        deployments, sets, pods = {}, {}, {}
        for service in sorted(BUSINESS_ROUTES):
            deployments[service] = await self.kube._request('GET', prefix + '/deployments/' + service)
            params = {'labelSelector': 'app=' + service, 'limit': 100}
            rs, ps = await asyncio.gather(self.kube._request('GET', prefix + '/replicasets', params=params),
                                         self.kube._request('GET', core + '/pods', params=params))
            if rs.get('metadata', {}).get('continue') or ps.get('metadata', {}).get('continue'):
                raise ValueError('Kubernetes inventory pagination exceeds bounded source scope')
            sets[service], pods[service] = rs['items'], ps['items']
        try:
            owned = project_inventory(deployments, sets, pods, target=self.kube.target, deployment_uids=self.deployment_uids)
        except ValueError:
            raise KubernetesUnavailable('kubernetes-business-inventory-not-currently-usable') from None
        config = json.loads(await self._http(self.prometheus_endpoint + '/api/v1/status/config', limit=TRACE_BYTES))
        if config.get('status') != 'success' or not matches_source_configuration(
                yaml.safe_load(config['data']['yaml']), identity['prometheus_configuration_hash']):
            raise ValueError('Kubernetes Prometheus source configuration changed')
        def metadata(body):
            return {k:v for k,v in body['metadata'].items() if k in
                    ('name', 'namespace', 'uid', 'generation', 'deletionTimestamp', 'ownerReferences')}
        # Only identity/image/ownership fields; never persist Pod env, credentials or volumes.
        api = {'deployments': {}, 'replicasets': {}, 'pods': {}}
        for service in BUSINESS_ROUTES:
            d = deployments[service]
            containers = [{k:c[k] for k in ('name','image')} for c in d['spec']['template']['spec']['containers']]
            api['deployments'][service] = {'metadata': metadata(d), 'spec': {'replicas': d['spec']['replicas'],
                'template': {'spec': {'containers': containers}}}}
            api['replicasets'][service] = [{'metadata':metadata(r)} for r in sets[service]]
            api['pods'][service] = [{'metadata':metadata(p), 'spec':{'containers':[
                {k:c[k] for k in ('name','image')} for c in p['spec']['containers']]},
                'status':{'containerStatuses':[{k:c[k] for k in ('name','imageID','state')}
                    for c in p.get('status',{}).get('containerStatuses',[])]}} for p in pods[service]]
        return {**owned, 'jaeger': {'running': True}, 'prometheus': {'running': True}, 'kubernetes_api': api}

    async def _trace(self, trace_id, entry, store):
        try:
            return await super()._trace(trace_id, entry, store)
        except KubernetesUnavailable:
            return None, 'kubernetes-trace-source-unavailable'

    async def _logs(self, identity, lower, upper):
        path = '/api/v1/namespaces/' + identity['namespace'] + '/pods/' + identity['pod_name'] + '/log'
        params = {'container': identity['service'].removesuffix('-service'), 'sinceTime': timestamp(lower),
                  'limitBytes': LOG_BYTES + 1, 'timestamps': 'false', 'previous': 'false'}
        pieces, size = [], 0
        try:
            async with asyncio.timeout(8):
                async with self.kube.client.stream('GET', path, params=params) as response:
                    if response.status_code >= 500:
                        raise KubernetesUnavailable('kubernetes-pod-logs-unavailable')
                    response.raise_for_status()
                    async for piece in response.aiter_bytes():
                        size += len(piece)
                        if size > LOG_BYTES: raise ValueError('Kubernetes log response exceeds byte budget')
                        pieces.append(piece)
        except (httpx.TransportError, TimeoutError):
            raise KubernetesUnavailable('kubernetes-pod-log-transport-unavailable') from None
        body = b''.join(pieces);received = now()
        rows, originals, counts = kubernetes_completions(body, identity, lower, upper, received)
        return rows, {**identity, 'collected_at': timestamp(received), 'selection': counts,
            'response_sha256': hashlib.sha256(body).hexdigest(), 'identity_records': originals}

    async def capture(self, store, *, source_state_ref, **kwargs):
        validate_policy(kwargs['policy'])
        state = store.read(source_state_ref)
        batch = store.read(state['last_batch_ref'])
        lower = utc_time(kwargs['group']['first_onset_at'])
        end = lower + timedelta(seconds=kwargs['policy']['evidence_window_seconds'])
        if utc_time(batch['bucket_end']) > end:
            # A current Pod query cannot bind a source bucket from outside the
            # incident window. Persist explicit missingness; never poison the
            # oldest pending job or widen its immutable evidence scope.
            return store.write('manifests', {
                'capture_version':'continuous-incident-evidence/v7', 'job_id':kwargs['job_id'],
                'intent_ref':kwargs['intent_ref'], 'profile':self.profile,
                'health_stream_id':stable_id(kwargs['health_identity']),
                'window_start':timestamp(lower), 'window_end':timestamp(end),
                'analyzed_at':timestamp(now()), 'actions_allowed':False,
                'unavailable_source':{'source_state_ref':source_state_ref,
                    'feature_batch_ref':state['last_batch_ref'],
                    'missing_reason':'accepted-source-past-evidence-window'}})
        base = store.read(await super().capture(store, **kwargs))
        inventory = {s:base['source_containers'][s] for s in BUSINESS_ROUTES}
        state = store.read(source_state_ref);batch = store.read(state['last_batch_ref'])
        normalization = store.read(batch['normalization_manifest_ref'])
        raw_refs = [r for r in normalization['raw_refs'] if store.read(r)['bucket_end'] == batch['bucket_end']]
        if len(raw_refs) != 3: raise ValueError('Kubernetes binding lacks three accepted HTTP buckets')
        pod_counters = []
        for reference in sorted(raw_refs):
            raw = store.read(reference);service = raw['service']
            url = self.prometheus_endpoint + '/api/v1/query?' + urlencode({
                'query': pod_query(service), 'time': utc_time(raw['bucket_end']).timestamp()})
            body = json.loads(await self._http(url, limit=TRACE_BYTES))
            verify_pod_counter(body, inventory[service], raw)
            pod_counters.append({'raw_ref': reference, 'expression': pod_query(service),
                                 'response': body, 'collected_at': timestamp(now())})
        after = await self.verify_source(kwargs['health_identity'])
        if any(after[s] != inventory[s] for s in BUSINESS_ROUTES):
            raise KubernetesUnavailable('kubernetes-pod-or-image-changed-during-capture')
        for reference in base['evidence_refs']['traces']:
            trace_pods(store.read(reference)['body'], inventory)
        base.update(capture_version='continuous-incident-evidence/v3',
            analyzed_at=timestamp(now()), physical_binding={'source_state_ref': source_state_ref,
                'feature_batch_ref': state['last_batch_ref'], 'pod_counters': pod_counters,
                'inventory_after': after['kubernetes_api']})
        return store.write('manifests', base)


def verify_kubernetes_binding(store, capture, health_identity):
    """Reproduce original UID/denominator/log/trace binding after journal authentication."""
    pack, profile = capture['physical_binding'], capture['profile']
    if (set(pack) != {'source_state_ref','feature_batch_ref','pod_counters','inventory_after'}
        or profile['profile_version'] != 'kubernetes-incident-evidence/v1'
        or health_identity['environment'] != 'local'
        or health_identity['endpoint_hash'] != stable_id({'endpoint':profile['prometheus_endpoint']})):
        raise ValueError('Kubernetes capture profile/source binding differs')
    target = Target(**profile['target'])
    owned = capture['source_containers'];api = owned['kubernetes_api']
    inventory = project_inventory(**api, target=target, deployment_uids=profile['deployment_uids'])
    final = project_inventory(**pack['inventory_after'], target=target, deployment_uids=profile['deployment_uids'])
    if final != inventory or any(owned[s] != inventory[s] for s in BUSINESS_ROUTES):
        raise ValueError('Kubernetes capture inventory differs from original API projections')
    state = store.read(pack['source_state_ref']);batch = store.read(pack['feature_batch_ref'])
    if (state['last_batch_ref'] != pack['feature_batch_ref'] or batch['identity'] != health_identity
        or batch['stream_id'] != stable_id(health_identity)):
        raise ValueError('Kubernetes binding selects a different accepted health batch')
    normalization = store.read(batch['normalization_manifest_ref'])
    expected = {r for r in normalization['raw_refs'] if store.read(r)['bucket_end'] == batch['bucket_end']}
    counters = pack['pod_counters']
    if (len(expected) != 3 or len(counters) != 3 or {x['raw_ref'] for x in counters} != expected
        or utc_time(batch['bucket_end']) > utc_time(capture['window_end'])):
        raise ValueError('Kubernetes binding changes or duplicates original HTTP bucket coverage')
    seen = set()
    for row in counters:
        raw = store.read(row['raw_ref']);service = raw['service'];seen.add(service)
        if (row['expression'] != pod_query(service) or raw['source_profile'] != health_identity['source_profile']
            or not utc_time(raw['collected_at']) <= utc_time(row['collected_at']) <= utc_time(capture['analyzed_at'])):
            raise ValueError('Kubernetes counter query/receipt differs from accepted source')
        verify_pod_counter(row['response'], inventory[service], raw)
    if seen != set(BUSINESS_ROUTES): raise ValueError('Kubernetes binding lacks a business service')
    logs = store.read(capture['evidence_refs']['logs'])
    receipts = logs['source_receipts']
    if len(receipts) != 3 or {r['service'] for r in receipts} != set(BUSINESS_ROUTES):
        raise ValueError('Kubernetes log receipt service coverage differs')
    for receipt in receipts:
        service = receipt['service']
        if any(receipt.get(k) != v for k,v in inventory[service].items()):
            raise ValueError('Kubernetes log receipt differs from API Pod inventory')
        originals = receipt['identity_records']
        body = b'\n'.join(json.dumps(r).encode() for r in originals)
        if len(body) > LOG_BYTES: raise ValueError('Kubernetes log identity projection exceeds byte budget')
        rows, rebuilt, _ = kubernetes_completions(body, inventory[service], utc_time(capture['window_start']),
                                                 utc_time(capture['window_end']), utc_time(receipt['collected_at']))
        if rebuilt != originals or rows != [r for r in logs['records'] if r['service'] == service]:
            raise ValueError('Kubernetes log UID projection does not reproduce business records')
    for reference in capture['evidence_refs']['traces']:
        trace_pods(store.read(reference)['body'], inventory)
    return inventory
