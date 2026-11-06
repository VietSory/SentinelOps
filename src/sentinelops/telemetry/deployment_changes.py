"""Observed image transitions, never experiment truth or an action approval.

An interval bounds when a change was observed; no exact historical deployment
clock is invented. These receipts alone do not bind Kubernetes to HTTP telemetry.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.remediation.domain import Blocked, Policy, fresh, image_digest
from sentinelops.remediation.registry import deployment_from_projection
from sentinelops.telemetry.core import stable_id, timestamp

MAX_INTERVAL_SECONDS = 180
FLAGS = {"known_good_approved": False, "physical_binding_verified": False,
         "rca_support_authorized": False, "action_dispatch_authorized": False}


def observer_boot():
    return stable_id(Path('/proc/sys/kernel/random/boot_id').read_text().strip())


def source_scope(source, target):
    return {"target": asdict(target), "environment": "local", "health_stream_id": source.stream_id,
            "health_policy_sha256": stable_id(source.policy),
            "source_root_sha256": stable_id({"root": str(source.store.root)})}


def _validate_observation(point, target):
    point.validate(Policy(target), before_dispatch=False)
    if (not isinstance(point.resource_version, str) or not 1 <= len(point.resource_version) <= 256
        or any(not isinstance(p.uid, str) or not 1 <= len(p.uid) <= 128
               or not isinstance(p.image_id, str) or len(p.image_id) > 1024 for p in point.pods)):
        raise Blocked('deployment-change-invalid-observation-scalars')
    for pod in point.pods:
        image_digest(pod.image)
    return point


def snapshot(store, ref, target):
    return _validate_observation(deployment_from_projection(store.read(ref)), target)


def verify_change(store, reference, *, source, target, as_of):
    """Reproduce one bounded transition, still without physical source authority."""
    return _verify_event(store, store.read(reference), reference, source=source, target=target, as_of=as_of)


def _verify_event(store, event, reference, *, source, target, as_of):
    validate_payload('payment-deployment-change.schema.json', event)
    baseline = store.read(event['baseline_ref'])
    validate_payload('payment-deployment-baseline.schema.json', baseline)
    expected = source_scope(source, target)
    if any(event[k] != v or baseline[k] != v for k, v in expected.items()):
        raise Blocked('deployment-change-source-or-target-mismatch')
    if (event['before_ref'] != baseline['before_ref'] or event['opened_at'] != baseline['opened_at']
        or event['observer_boot_sha256'] != baseline['observer_boot_sha256']):
        raise Blocked('deployment-change-baseline-mismatch')
    before = snapshot(store, baseline['before_ref'], target)
    after = snapshot(store, event['after_ref'], target)
    if (before.observed_generation != before.generation
        or not before.ready_for(before.image, prior_generation=0, old_pods=())
        or before.target != target or after.target != target
        or after.generation != before.generation + 1 or after.image == before.image
        or before.container_index != after.container_index
        or before.resource_version == after.resource_version):
        raise Blocked('deployment-change-not-one-observed-image-transition')
    opened, received, recorded = map(utc_time, (baseline['opened_at'], baseline['recorded_at'], event['recorded_at']))
    lower, upper = map(utc_time, (event['change_not_before'], event['change_not_after']))
    if (lower != opened or after.observed_at != event['change_not_after']
        or not opened <= utc_time(before.observed_at) <= received <= upper <= recorded <= as_of
        or not timedelta(0) < upper-lower <= timedelta(seconds=MAX_INTERVAL_SECONDS)):
        raise Blocked('deployment-change-clock-or-interval-mismatch')
    return {'before': before, 'after': after, 'change_interval': (lower, upper),
            'available_at': recorded, 'source_ref': reference, **FLAGS}


class DeploymentChangeReview:
    """Read-only observer; an original source and a separate evidence store."""
    def __init__(self, source, kubernetes, store, target, *, clock=lambda: datetime.now(timezone.utc)):
        original = source.store.root
        if store.root == original or store.root in original.parents or original in store.root.parents:
            raise ValueError('deployment evidence store must be separate from original health source')
        self.source, self.kube, self.store, self.target, self.clock = source, kubernetes, store, target, clock

    def _write_snapshot(self, observation):
        _validate_observation(observation, self.target)
        if observation.target != self.target:
            raise Blocked('deployment-change-target-mismatch')
        return self.store.write('manifests', {'observation_version': 'payment-deployment/v1',
                                            'observation': observation.projection()})

    async def open(self):
        started = self.clock()
        await self.kube.require_observer()
        before = await self.kube.observe()
        ended = self.clock()
        if not started <= utc_time(before.observed_at) <= ended:
            raise Blocked('deployment-change-observer-clock-mismatch')
        if (before.observed_generation != before.generation
            or not before.ready_for(before.image, prior_generation=0, old_pods=())):
            raise Blocked('deployment-change-baseline-not-settled')
        record = {'record_version': 'payment-deployment-baseline/v1', **source_scope(self.source, self.target),
                  'observer_boot_sha256': observer_boot(), 'before_ref': self._write_snapshot(before),
                  'opened_at': timestamp(started), 'recorded_at': timestamp(ended), **FLAGS}
        validate_payload('payment-deployment-baseline.schema.json', record)
        ref = self.store.write('manifests', record)
        return {'status': 'BASELINE_OBSERVED', 'baseline_ref': ref, **FLAGS}

    async def complete(self, baseline_ref):
        record = self.store.read(baseline_ref)
        validate_payload('payment-deployment-baseline.schema.json', record)
        expected = source_scope(self.source, self.target)
        if any(record[k] != v for k, v in expected.items()) or record['observer_boot_sha256'] != observer_boot():
            raise Blocked('deployment-change-source-target-or-host-mismatch')
        started = self.clock()
        fresh(record['opened_at'], started, MAX_INTERVAL_SECONDS)
        before = snapshot(self.store, record['before_ref'], self.target)
        if not utc_time(record['opened_at']) <= utc_time(before.observed_at) <= utc_time(record['recorded_at']):
            raise Blocked('deployment-change-baseline-clock-mismatch')
        await self.kube.require_observer()
        after = await self.kube.observe()
        ended = self.clock()
        if not started <= utc_time(after.observed_at) <= ended:
            raise Blocked('deployment-change-observer-clock-mismatch')
        fresh(record['opened_at'], ended, MAX_INTERVAL_SECONDS)
        after_ref = self._write_snapshot(after)
        if after.generation == before.generation and after.image == before.image:
            return {'status': 'PENDING', 'baseline_ref': baseline_ref, **FLAGS}
        event = {'record_version': 'payment-deployment-change/v1', **expected,
                 'observer_boot_sha256': record['observer_boot_sha256'], 'before_ref': record['before_ref'],
                 'opened_at': record['opened_at'], 'recorded_at': timestamp(ended), 'baseline_ref': baseline_ref,
                 'after_ref': after_ref, 'change_not_before': record['opened_at'],
                 'change_not_after': after.observed_at, **FLAGS}
        # Validate and reproduce before returning a usable receipt. Immutable
        # rejected observations remain diagnostic evidence, never approval.
        validate_payload('payment-deployment-change.schema.json', event)
        ref = 'artifact://manifests/' + stable_id(event) + '.json'
        verified = _verify_event(self.store, event, ref, source=self.source, target=self.target, as_of=ended)
        if self.store.write('manifests', event) != ref:
            raise ValueError('deployment event content identity mismatch')
        return {'status': 'CHANGE_OBSERVED', 'change_ref': ref,
                'observed_before_image': verified['before'].image,
                'observed_after_image': verified['after'].image, **FLAGS}
