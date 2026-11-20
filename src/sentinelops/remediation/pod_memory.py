"""One bounded 64Mi -> 256Mi action, via verified OOM or explicit manual recovery.

This controller observes Kubernetes facts, not ML predictions. Only the dedicated
demo Deployment is supported. Its image, requests, replicas and other settings
stay pinned. Immutable intent precedes PATCH; uncertain sends are never replayed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile

import httpx

from sentinelops.contracts import utc_time
from sentinelops.incidents.domain import IncidentNotFound, IncidentWrite
from sentinelops.remediation import approvals
from sentinelops.remediation.domain import Blocked, image_digest
from sentinelops.telemetry.core import stable_id, timestamp

RUNBOOK = 'pod-memory-limit-demo/v1'
NAME = 'sentinelops-oom-demo'
NAMESPACE = 'sentinelops-b-actions'
BEFORE, AFTER = '64Mi', '256Mi'
TERMINAL = {'recovered', 'blocked', 'escalated', 'rejected', 'expired'}


class ObservationPending(RuntimeError):
    """An ordinary rollout gap, never evidence of recovery."""


@dataclass(frozen=True)
class MemoryTarget:
    cluster_uid: str
    namespace_uid: str
    deployment_uid: str
    image: str
    namespace: str = NAMESPACE
    deployment: str = NAME
    container: str = 'oom-demo'

    def __post_init__(self):
        image_digest(self.image)
        if (self.namespace != NAMESPACE or self.deployment != NAME or self.container != 'oom-demo'
            or any(not isinstance(v, str) or not 1 <= len(v) <= 128
                   for v in (self.cluster_uid, self.namespace_uid, self.deployment_uid))):
            raise ValueError('only the explicitly bound OOM demo target is supported')


def review_change(proposal):
    """Exact change displayed for human approval; no client patch parameters."""
    if proposal.get('executor_runbook') != RUNBOOK:
        return None
    try:
        target = MemoryTarget(**proposal['target'])
        change = proposal['resource_change']
        if (change != {'memory_before': BEFORE, 'memory_after': AFTER}
            or proposal['cause'] != 'OOMKilled' or not proposal['pod_uid']
            or proposal['current_image'] != target.image):
            return None
        return {'kind': 'memory_limit', **change, 'image': target.image,
                'deployment': target.deployment, 'container': target.container,
                'pod_uid': proposal['pod_uid'], 'cause': 'OOMKilled'}
    except (Blocked, ValueError, KeyError, TypeError):
        return None


def recovery_change(proposal):
    """Bounded human recovery choice, without claiming OOM or failed-pod proof."""
    if proposal.get('executor_runbook') != RUNBOOK:
        return None
    try:
        target = MemoryTarget(**proposal['target'])
        change = proposal['resource_change']
        if (change != {'memory_before': BEFORE, 'memory_after': AFTER}
            or proposal['current_image'] != target.image):
            return None
        return {'kind': 'memory_limit', **change, 'image': target.image,
                'namespace': target.namespace, 'deployment': target.deployment, 'container': target.container}
    except (Blocked, ValueError, KeyError, TypeError):
        return None


def template_hash(deployment):
    template = deepcopy(deployment['spec']['template'])
    template['spec']['containers'][0]['resources']['limits']['memory'] = '<bounded-memory>'
    return stable_id(template)


def project(target, deployment, pods, replica_sets, node, hpas, *, now):
    """Validate actual Kubernetes ownership/limits; never infer OOM from absence."""
    spec, meta = deployment['spec'], deployment['metadata']
    if (meta['uid'] != target.deployment_uid or meta['name'] != NAME
        or meta.get('namespace') != NAMESPACE or spec.get('replicas') != 1
        or spec.get('paused', False) or meta.get('deletionTimestamp')
        or spec['selector'] != {'matchLabels': {'app': NAME}}
        or spec['template']['metadata'].get('labels', {}).get('app') != NAME
        or spec['template']['metadata'].get('labels', {}).get('sentinelops.io/demo') != 'oom'
        or len(spec['template']['spec']['containers']) != 1
        or spec['template']['spec'].get('initContainers')
        or spec['strategy'] != {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 0, 'maxUnavailable': 1}}):
        raise Blocked('oom-demo-deployment-outside-policy')
    container = spec['template']['spec']['containers'][0]
    if (container['name'] != target.container or container['image'] != target.image
        or container['resources'] not in (
            {'requests': {'cpu': '25m', 'memory': '32Mi'}, 'limits': {'cpu': '250m', 'memory': BEFORE}},
            {'requests': {'cpu': '25m', 'memory': '32Mi'}, 'limits': {'cpu': '250m', 'memory': AFTER}})):
        raise Blocked('oom-demo-resources-or-image-outside-policy')
    if any(h['spec']['scaleTargetRef'].get('kind') == 'Deployment'
           and h['spec']['scaleTargetRef'].get('name') == NAME for h in hpas):
        raise Blocked('oom-demo-hpa-present')
    live = [p for p in pods if not p['metadata'].get('deletionTimestamp')]
    if len(live) != 1:
        raise ObservationPending('oom-demo-requires-one-live-pod')
    pod = live[0]
    owner = next((o for o in pod['metadata'].get('ownerReferences', []) if o.get('controller')), {})
    rs = next((r for r in replica_sets if r['metadata']['uid'] == owner.get('uid')), None)
    if (owner.get('kind') != 'ReplicaSet' or rs is None
        or not any(o.get('controller') and o.get('kind') == 'Deployment'
                   and o.get('uid') == target.deployment_uid for o in rs['metadata'].get('ownerReferences', []))
        or pod['spec']['nodeName'] != node['metadata']['name']
        or len(pod['spec']['containers']) != 1):
        raise Blocked('oom-demo-pod-owner-mismatch')
    if any(pod['spec']['containers'][0].get(k) != container.get(k) for k in ('name', 'image', 'resources')):
        raise ObservationPending('oom-demo-waiting-for-template-pod')
    conditions = {c['type']: c['status'] for c in node['status'].get('conditions', [])}
    if conditions.get('Ready') != 'True' or conditions.get('MemoryPressure') != 'False':
        raise Blocked('oom-demo-node-unhealthy-or-memory-pressure')
    states = [s for s in pod['status'].get('containerStatuses', []) if s['name'] == target.container]
    if len(states) != 1:
        raise ObservationPending('oom-demo-container-status-unavailable')
    state = states[0]
    termination = state.get('state', {}).get('terminated') or state.get('lastState', {}).get('terminated') or {}
    oom = termination.get('reason') == 'OOMKilled' and termination.get('exitCode') == 137
    age = (now - utc_time(termination['finishedAt'])).total_seconds() if oom else None
    return {'observed_at': timestamp(now), 'target': asdict(target),
            'resource_version': meta['resourceVersion'], 'generation': meta['generation'],
            'observed_generation': deployment.get('status', {}).get('observedGeneration', 0),
            'template_sha256': template_hash(deployment),
            'memory': container['resources']['limits']['memory'], 'pod_uid': pod['metadata']['uid'],
            'restart_count': state['restartCount'], 'oom': oom,
            'oom_finished_at': termination.get('finishedAt') if oom else None,
            'fresh_oom': oom and 0 <= age <= 180,
            'ready': bool(state.get('ready')) and pod['status'].get('phase') == 'Running'
                     and deployment.get('status', {}).get('readyReplicas') == 1
                     and deployment.get('status', {}).get('updatedReplicas') == 1
                     and deployment.get('status', {}).get('observedGeneration') == meta['generation']}


def memory_patch(target, observation):
    if observation['target'] != asdict(target) or observation['memory'] != BEFORE:
        raise Blocked('oom-demo-patch-target-changed')
    pointer = '/spec/template/spec/containers/0'
    return [
        {'op': 'test', 'path': '/metadata/uid', 'value': target.deployment_uid},
        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': observation['resource_version']},
        {'op': 'test', 'path': '/metadata/generation', 'value': observation['generation']},
        {'op': 'test', 'path': '/spec/replicas', 'value': 1},
        {'op': 'test', 'path': pointer + '/name', 'value': target.container},
        {'op': 'test', 'path': pointer + '/image', 'value': target.image},
        {'op': 'test', 'path': pointer + '/resources', 'value': {
            'requests': {'cpu': '25m', 'memory': '32Mi'}, 'limits': {'cpu': '250m', 'memory': BEFORE}}},
        {'op': 'replace', 'path': pointer + '/resources/limits/memory', 'value': AFTER},
    ]


class KubernetesMemory:
    def __init__(self, target, *, server, token_file, ca_file):
        from sentinelops.remediation.kubernetes import configured_server
        configured_server(server)
        self.target, self.token_file = target, Path(token_file)
        self.client = httpx.AsyncClient(base_url=server, verify=str(ca_file), timeout=5, follow_redirects=False)
        self.path = f'/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{NAME}'
        self.http = httpx.AsyncClient(timeout=2, follow_redirects=False, trust_env=False)

    async def close(self):
        await self.client.aclose()
        await self.http.aclose()

    async def request(self, method, path, **kwargs):
        response = await self.client.request(method, path, headers={
            'Authorization': 'Bearer ' + self.token_file.read_text().strip(),
            'Content-Type': 'application/json-patch+json' if method == 'PATCH' else 'application/json'}, **kwargs)
        if response.status_code not in (200, 201):
            raise Blocked('oom-demo-kubernetes-request-failed')
        return response.json()

    async def observe(self, now):
        cluster = await self.request('GET', '/api/v1/namespaces/kube-system')
        ns = await self.request('GET', '/api/v1/namespaces/' + NAMESPACE)
        if cluster['metadata']['uid'] != self.target.cluster_uid or ns['metadata']['uid'] != self.target.namespace_uid:
            raise Blocked('oom-demo-cluster-or-namespace-mismatch')
        deployment = await self.request('GET', self.path)
        args = {'params': {'labelSelector': 'app=' + NAME}}
        pods = await self.request('GET', f'/api/v1/namespaces/{NAMESPACE}/pods', **args)
        rs = await self.request('GET', f'/apis/apps/v1/namespaces/{NAMESPACE}/replicasets', **args)
        hpas = await self.request('GET', f'/apis/autoscaling/v2/namespaces/{NAMESPACE}/horizontalpodautoscalers')
        live = [p for p in pods['items'] if not p['metadata'].get('deletionTimestamp')]
        if len(live) != 1 or not live[0].get('spec', {}).get('nodeName'):
            raise ObservationPending('oom-demo-requires-one-scheduled-pod')
        node = await self.request('GET', '/api/v1/nodes/' + live[0]['spec']['nodeName'])
        return project(self.target, deployment, pods['items'], rs['items'], node, hpas['items'], now=now)

    async def patch(self, observation, *, dry_run):
        return await self.request('PATCH', self.path, json=memory_patch(self.target, observation),
                                  params={'dryRun': 'All'} if dry_run else {})

    async def probe(self):
        result = await self.http.get(f'http://{NAME}.{NAMESPACE}.svc.cluster.local:8080/health')
        return result.status_code == 200 and result.json() == {'ok': True, 'allocation_mib': 128}


def read_state(store, proposal_id):
    approvals._slot(store, 'used', proposal_id)  # Validate opaque identity before path use.
    path = store.root / 'pod-memory' / (proposal_id + '.json')
    try:
        ref = json.loads(path.read_text())['ref']
    except FileNotFoundError:
        return None
    state = store.read(ref)
    if state['proposal_id'] != proposal_id or state['runbook'] != RUNBOOK:
        raise ValueError('memory action state identity mismatch')
    return state


def write_state(store, state):
    ref = store.write('manifests', state)
    folder = store.root / 'pod-memory'
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=folder, mode='w', delete=False) as stream:
        stream.write(json.dumps({'ref': ref}))
        stream.flush()
        os.fsync(stream.fileno())
        name = stream.name
    os.replace(name, folder / (state['proposal_id'] + '.json'))
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return state


class MemoryController:
    def __init__(self, *, target, kube, store, incidents, kill_switch, enabled=False,
                 clock=lambda: datetime.now(timezone.utc)):
        self.target, self.kube, self.store, self.incidents = target, kube, store, incidents
        self.kill_switch, self.enabled, self.clock = Path(kill_switch), enabled, clock

    def _save(self, state, **changes):
        previous = self.store.write('manifests', state)
        return write_state(self.store, {**state, **changes, 'previous_ref': previous,
                                        'updated_at': timestamp(self.clock())})

    def _enabled(self):
        if not self.enabled or self.kill_switch.exists():
            raise Blocked('oom-demo-disabled-or-kill-switch')

    async def _incident(self, proposal, stage, *, state='incident', availability='UNKNOWN', ref=None,
                        expected_revision=None):
        try:
            current = await self.incidents.get(proposal['incident_id'])
        except IncidentNotFound:
            current = None
        if expected_revision is not None and (current is None or current['revision'] != expected_revision):
            raise Blocked('oom-demo-incident-revision-changed')
        if current and current['context']['lifecycle_stage'] == stage:
            return current
        action = read_state(self.store, proposal['proposal_id']) or {}
        manual = action.get('approval_mode') == approvals.MANUAL_MEMORY_RECOVERY
        verified_oom = not manual and review_change(proposal) is not None and bool(proposal.get('observation_ref'))
        source_ref = (proposal['observation_ref'] if verified_oom
                      else self.store.write('manifests', proposal))
        references = list(dict.fromkeys([source_ref] + ([ref] if ref else [])))
        fact = ('Kubernetes observed OOMKilled (exit 137) in the bounded memory demo. '
                'Proposed memory limit: 64Mi -> 256Mi; image unchanged.' if verified_oom else
                'Bounded manual memory recovery: 64Mi -> 256Mi; image unchanged. '
                'OOM cause and failed-pod evidence are not established by this recovery approval.')
        payload = {'schema_version': '1.0', 'incident_id': proposal['incident_id'],
            'detected_at': proposal['created_at'], 'state': state, 'severity': 'high',
            'affected_services': [NAME], 'service_signals': [], 'root_cause_ranking': [],
            'evidence': [{'id': 'kubernetes-oom' if verified_oom else 'manual-memory-recovery',
                'type': 'log', 'service': NAME, 'observed_at': proposal['created_at'],
                'source_ref': source_ref, 'fact': fact}]}
        context = {'availability': availability, 'source_refs': references,
            'config_refs': ['config://pod-memory-limit-demo/v1'],
            'recovery_source': ('manual' if manual else 'action') if state in ('recovering', 'recovered') else None,
            'trigger_sources': [{'source': 'kubernetes' if verified_oom else 'manual',
                'signal_id': proposal['proposal_id'], 'emitted_at': proposal['created_at'], 'source_ref': source_ref}]}
        return await self.incidents.write(IncidentWrite(payload, stage, current['revision'] if current else 0,
            'oom-' + proposal['proposal_id'][:32] + '-' + stage, 'pod-memory-demo-controller',
            ('verified-kubernetes-oom-' if verified_oom else 'manual-memory-recovery-') + stage.lower(), context))

    async def tick(self):
        import fcntl  # The worker requires Linux; importing HTTP review does not.
        self.store.root.mkdir(parents=True, exist_ok=True)
        # Same mounted filesystem, exclusive writer across processes and restarts.
        with (self.store.root / 'pod-memory-controller.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'phase': 'waiting', 'reason': 'controller-already-active'}
            proposals = []
            for path in (self.store.root / 'manifests').glob('*.json'):
                value = self.store.read('artifact://manifests/' + path.name)
                if value.get('executor_runbook') == RUNBOOK and value.get('proposal_version') == approvals.PROPOSAL_VERSION:
                    if value['target'] == asdict(self.target):
                        proposals.append(value)
            proposals.sort(key=lambda p: p['created_at'])
            pending = [p for p in proposals if not (read_state(self.store, p['proposal_id']) or {}).get('finalized')]
            if pending:
                if len(pending) != 1:
                    raise Blocked('oom-demo-conflicting-pending-proposals')
                return await self._advance(pending[0])
            self._enabled()
            observed = await self.kube.observe(self.clock())
            if observed['memory'] != BEFORE or observed['ready'] or not observed['fresh_oom']:
                return {'phase': 'observing', 'reason': 'no-current-oom-at-low-limit'}
            # A target gets one attempt per pod/generation. A rehearsal reset is
            # an explicit operator change; native container restarts never retry.
            if any(p.get('pod_uid') == observed['pod_uid'] or p.get('generation') == observed['generation'] for p in proposals):
                return {'phase': 'observing', 'reason': 'episode-already-handled'}
            if proposals and (self.clock() - utc_time((read_state(self.store, proposals[-1]['proposal_id']) or {})['updated_at'])).total_seconds() < 60:
                return {'phase': 'observing', 'reason': 'cooldown'}
            ref = self.store.write('observations', observed)
            now = self.clock()
            identifier = stable_id({'runbook': RUNBOOK, 'observation_ref': ref})
            proposal = {'proposal_version': approvals.PROPOSAL_VERSION, 'proposal_id': identifier,
                'executor_runbook': RUNBOOK, 'runbook_id': 'pod-oom-memory-limit',
                'incident_id': 'INC-OOM-' + identifier, 'incident_revision': 1,
                'created_at': timestamp(now), 'expires_at': timestamp(now + timedelta(minutes=15)),
                'target': asdict(self.target), 'current_image': self.target.image,
                'resource_change': {'memory_before': BEFORE, 'memory_after': AFTER}, 'cause': 'OOMKilled',
                'pod_uid': observed['pod_uid'], 'generation': observed['generation'],
                'template_sha256': observed['template_sha256'], 'observation_ref': ref,
                'decision': 'oncall', 'symptoms': {'severity': 'high'},
                'demo_mode': True, 'G6_accepted': False, 'q09_eligible': False}
            # Publish the immutable identity first. A restart must resume this
            # episode, including an interrupted initial incident write.
            self.store.write('manifests', proposal)
            state = write_state(self.store, {'runbook': RUNBOOK, 'proposal_id': identifier,
                'phase': 'pending', 'updated_at': timestamp(now), 'dispatches': 0, 'reason': 'oncall-approval-required'})
            await self._incident(proposal, 'OPEN')
            return state

    async def _advance(self, proposal):
        state = read_state(self.store, proposal['proposal_id']) or {'runbook': RUNBOOK,
            'proposal_id': proposal['proposal_id'], 'phase': 'pending', 'dispatches': 0}
        # Terminal intent is durable even if the database or final pointer save
        # failed. Finalization errors must never reopen dispatch/verification.
        if state['phase'] in TERMINAL:
            return await self._finish(proposal, state, state['phase'], state['reason'])
        try:
            if state['phase'] != 'pending' and self.clock() >= utc_time(state['deadline']):
                return await self._finish(proposal, state, 'escalated', 'verification-timeout')
            if state['phase'] == 'pending':
                try:
                    await self.incidents.get(proposal['incident_id'])
                except IncidentNotFound:
                    await self._incident(proposal, 'OPEN')
                self._enabled()
                if self.clock() >= utc_time(proposal['expires_at']):
                    return await self._finish(proposal, state, 'expired', 'approval-expired')
                receipt = approvals.read_slot(self.store, 'approval', proposal['proposal_id'])
                if receipt is None:
                    return state
                if receipt['proposal_sha256'] != stable_id(proposal):
                    raise Blocked('oom-demo-approval-hash-mismatch')
                if receipt['decision'] == 'reject':
                    return await self._finish(proposal, state, 'rejected', 'operator-rejected')
                if (receipt['decision'] != 'approve' or not receipt['approved_by'].strip()
                    or not utc_time(proposal['created_at']) <= utc_time(receipt['approved_at']) <= self.clock()):
                    raise Blocked('oom-demo-invalid-approval')
                mode = receipt.get('approval_mode')
                manual = mode == approvals.MANUAL_MEMORY_RECOVERY
                if mode is not None and (not manual or recovery_change(proposal) is None
                                         or proposal['target'] != asdict(self.target)):
                    raise Blocked('oom-demo-invalid-manual-recovery')
                if manual:
                    state = self._save(state, approval_mode=mode)
                current = await self.incidents.get(proposal['incident_id'])
                if current['revision'] != proposal['incident_revision']:
                    raise Blocked('oom-demo-incident-revision-changed')
                observed = await self.kube.observe(self.clock())
                if (observed['memory'] != BEFORE or observed['target'] != asdict(self.target)
                    or (not manual and (observed['ready'] or not observed['fresh_oom']
                        or observed['pod_uid'] != proposal['pod_uid'] or observed['generation'] != proposal['generation']
                        or observed['template_sha256'] != proposal['template_sha256']))):
                    raise Blocked('oom-demo-target-or-evidence-changed')
                if manual:
                    state = self._save(state, recovery_baseline=observed)
                await self.kube.patch(observed, dry_run=True)
                self._enabled()
                if self.clock() >= utc_time(proposal['expires_at']):
                    raise Blocked('oom-demo-approval-expired-before-dispatch')
                # Persist consumed approval and intent BEFORE the only mutation.
                used = {'proposal_id': proposal['proposal_id'], 'approval_sha256': stable_id(receipt),
                        'runbook': RUNBOOK, 'before': observed, 'claimed_at': timestamp(self.clock())}
                if manual:
                    used['approval_mode'] = mode
                approvals.create_slot(self.store, 'used', proposal['proposal_id'], used)
                state = self._save(state, phase='dispatching', dispatches=1, before=observed,
                    deadline=timestamp(self.clock() + timedelta(minutes=5)), reason='approved-memory-limit-change')
                rca = await self._incident(proposal, 'RCA_READY', expected_revision=proposal['incident_revision'])
                planned = await self._incident(proposal, 'MITIGATION_PLANNED', expected_revision=rca['revision'])
                mitigating = await self._incident(proposal, 'MITIGATING', state='recovering',
                                                   expected_revision=planned['revision'])
                self._enabled()
                if self.clock() >= utc_time(proposal['expires_at']):
                    raise Blocked('oom-demo-approval-expired-before-dispatch')
                current = await self.incidents.get(proposal['incident_id'])
                if current['revision'] != mitigating['revision']:
                    raise Blocked('oom-demo-incident-revision-changed-before-dispatch')
                # Status updates can advance resourceVersion while the durable
                # incident transitions are saved. Reobserve all policy guards
                # and persist the refreshed CAS preconditions before sending.
                observed = await self.kube.observe(self.clock())
                baseline = state['recovery_baseline'] if manual else proposal
                if (observed['memory'] != BEFORE or observed['target'] != asdict(self.target)
                    or (not manual and (observed['ready'] or not observed['fresh_oom']))
                    or observed['pod_uid'] != baseline['pod_uid'] or observed['generation'] != baseline['generation']
                    or observed['template_sha256'] != baseline['template_sha256']):
                    raise Blocked('oom-demo-target-or-evidence-changed-before-dispatch')
                state = self._save(state, before=observed)
                self._enabled()
                if self.clock() >= utc_time(proposal['expires_at']):
                    raise Blocked('oom-demo-approval-expired-before-dispatch')
                await self.kube.patch(observed, dry_run=False)
                state = self._save(state, phase='verifying', reason='waiting-for-replacement-pod')
            elif state['phase'] == 'dispatching':
                # The previous PATCH may have succeeded. Never resend it.
                observed = await self.kube.observe(self.clock())
                if observed['memory'] != AFTER:
                    return await self._finish(proposal, state, 'escalated', 'uncertain-dispatch-needs-operator')
                state = self._save(state, phase='verifying', reason='reconciled-without-resend')
            return await self._verify(proposal, state)
        except Blocked as exc:
            durable = read_state(self.store, proposal['proposal_id'])
            if durable and durable['phase'] in TERMINAL:
                raise
            return await self._finish(proposal, state, 'escalated' if state['dispatches'] else 'blocked', str(exc))
        except ObservationPending:
            return self._save(state, reason='waiting-for-kubernetes-observation', healthy_since=None,
                              successful_probes=0, last_probe_at=None)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, OSError):
            durable = read_state(self.store, proposal['proposal_id'])
            if durable and durable['phase'] in TERMINAL:
                raise
            # Preserve dispatching/verifying for observation after outages; an
            # uncertain network failure must never reopen approval or send again.
            if state['phase'] in ('dispatching', 'verifying'):
                return self._save(state, reason='observation-or-dispatch-outcome-unavailable',
                                  healthy_since=None, successful_probes=0, last_probe_at=None)
            return await self._finish(proposal, state, 'blocked', 'approval-or-evidence-unavailable')

    async def _verify(self, proposal, state):
        if self.clock() >= utc_time(state['deadline']):
            return await self._finish(proposal, state, 'escalated', 'verification-timeout')
        observed = await self.kube.observe(self.clock())
        baseline = state['recovery_baseline'] if state.get('approval_mode') == approvals.MANUAL_MEMORY_RECOVERY else proposal
        if (observed['memory'] != AFTER or observed['generation'] != baseline['generation'] + 1
            or observed['template_sha256'] != baseline['template_sha256']):
            raise Blocked('oom-demo-unexpected-post-action-change')
        if not observed['ready'] or observed['pod_uid'] == baseline['pod_uid']:
            return self._save(state, reason='waiting-for-ready-replacement', healthy_since=None,
                              successful_probes=0, last_probe_at=None)
        if observed['oom'] or observed['restart_count'] != 0 or not await self.kube.probe():
            return self._save(state, reason='post-action-http-or-restart-check-failed', healthy_since=None,
                              successful_probes=0, last_probe_at=None)
        since = state.get('healthy_since')
        if (state.get('verified_pod_uid') != observed['pod_uid'] or not state.get('last_probe_at')
            or not 0 < (self.clock() - utc_time(state['last_probe_at'])).total_seconds() <= 5):
            since = None
        ref = self.store.write('observations', {**observed, 'http_success': True})
        state = self._save(state, phase='verifying', verified_pod_uid=observed['pod_uid'],
            healthy_since=since or timestamp(self.clock()), last_probe_ref=ref,
            successful_probes=(state.get('successful_probes', 0) if since else 0) + 1,
            last_probe_at=timestamp(self.clock()), reason='http-and-pod-verification')
        await self._incident(proposal, 'VERIFYING', state='recovering', ref=ref)
        if (self.clock() - utc_time(state['healthy_since'])).total_seconds() >= 30 and state['successful_probes'] >= 10:
            return await self._finish(proposal, state, 'recovered', 'stable-ready-pod-and-30s-http-success')
        return state

    async def _finish(self, proposal, state, phase, reason):
        # Even a kill switch/expiry that prevents dispatch retains the explicit
        # human mode in the terminal audit; this never authorizes an action.
        if state.get('approval_mode') is None:
            receipt = approvals.read_slot(self.store, 'approval', proposal['proposal_id'])
            if (receipt and receipt.get('approval_mode') == approvals.MANUAL_MEMORY_RECOVERY
                and receipt.get('decision') == 'approve' and receipt.get('proposal_sha256') == stable_id(proposal)
                and recovery_change(proposal) is not None and proposal['target'] == asdict(self.target)):
                state = {**state, 'approval_mode': approvals.MANUAL_MEMORY_RECOVERY}
        state = self._save(state, phase=phase, reason=reason)
        outcome = {'runbook': RUNBOOK, 'proposal_id': proposal['proposal_id'], 'phase': phase,
                   'reason': reason, 'dispatches': state['dispatches'], 'updated_at': state['updated_at']}
        if state.get('approval_mode') == approvals.MANUAL_MEMORY_RECOVERY:
            outcome['approval_mode'] = state['approval_mode']
        if approvals.read_slot(self.store, 'outcome', proposal['proposal_id']) is None:
            approvals.create_slot(self.store, 'outcome', proposal['proposal_id'], outcome)
        if phase == 'recovered':
            current = await self.incidents.get(proposal['incident_id'])
            if current['context']['lifecycle_stage'] == 'CLOSED' and (
                current['payload']['state'] != 'recovered'
                or current['context']['recovery_source'] != ('manual' if state.get('approval_mode') == approvals.MANUAL_MEMORY_RECOVERY else 'action')
                or current['context']['availability'] != 'ELIGIBLE'
            ):
                raise Blocked('oom-demo-terminal-incident-changed')
            if current['context']['lifecycle_stage'] != 'CLOSED':
                await self._incident(proposal, 'RECOVERED', state='recovered', availability='ELIGIBLE', ref=state['last_probe_ref'])
                await self._incident(proposal, 'CLOSED', state='recovered', availability='ELIGIBLE', ref=state['last_probe_ref'])
        else:
            await self._incident(proposal, 'ESCALATED', state='escalated')
        return self._save(state, finalized=True)
