"""Raw-codec/Kubernetes projections are software fixtures, not product admission."""
import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from sentinelops.contracts import validate_payload
from sentinelops.remediation.domain import Blocked
from sentinelops.remediation.image_review import ImageEvidenceReview
from sentinelops.remediation.registry import KnownGoodRegistry
from sentinelops.telemetry.core import timestamp
from sentinelops.telemetry.store import ArtifactStore
from test_remediation_kubernetes import GOOD, TARGET, observation
from test_remediation_verification import RawLedgerFixture, START


class Observer:
    def __init__(self, clock):
        self.clock = clock
        self.allowed = True
        base = observation()
        self.value = replace(base, image=GOOD, pods=(replace(base.pods[0], image=GOOD),))

    async def require_observer(self):
        if not self.allowed:
            raise Blocked('fixture-writer-credential')

    async def observe(self):
        return replace(self.value, observed_at=timestamp(self.clock()))


def setup(tmp_path, **kwargs):
    clock = [START]
    source = RawLedgerFixture(tmp_path / 'source', [0] * 6, **kwargs)
    kube = Observer(lambda: clock[0])
    review = ImageEvidenceReview(source, kube, ArtifactStore(tmp_path / 'review'), TARGET,
                                 environment='fixture', clock=lambda: clock[0])
    return review, source, kube, clock


def test_evidence_requires_three_whole_current_raw_verified_buckets_and_separate_approval(tmp_path):
    async def run():
        review, source, kube, clock = setup(tmp_path)
        opened = await review.open()
        assert (await review.complete(opened['review_ref']))['status'] == 'PENDING'
        clock[0] += timedelta(minutes=5, seconds=38)
        result = await review.complete(opened['review_ref'])
        assert result['status'] == 'AWAITING_OPERATOR_REVIEW'
        evidence = result['evidence']
        validate_payload('payment-image-evidence.schema.json', evidence)
        assert evidence['physical_binding_verified'] is evidence['known_good_approved'] is False
        assert evidence['action_dispatch_authorized'] is False
        assert not hasattr(kube, 'patch')  # No mutation capability required by the collector.
        assert result == await review.complete(opened['review_ref'])  # Same inputs/cutoff, immutable refs.
        # A candidate/card cannot be selected as the existing approved ledger.
        with pytest.raises(ValueError):
            KnownGoodRegistry(review.store, result['evidence_ref'], approved_by='fixture-operator', source=source)
        # Only this test constructs a separate approval fixture, never production code.
        record = {'ledger_version': 'approved-payment-images/v1', 'approved_by': 'fixture-operator',
                  'approved_at': timestamp(clock[0] + timedelta(seconds=1)),
                  'approval_scope': 'known-good-image-only', 'action_dispatch_authorized': False,
                  'images': [{k: evidence[k] for k in ('target', 'image', 'environment', 'before_ref', 'after_ref', 'health_ref')}]}
        registry = KnownGoodRegistry(review.store, review.store.write('manifests', record),
                                     approved_by='fixture-operator', source=source, environment='fixture')
        resolved = await registry.resolve(TARGET, incident_at=clock[0] + timedelta(seconds=2))
        assert resolved.image == GOOD
        # Source corruption is still rejected when the approved registry replays a current certificate.
        next((source.store.root / 'raw').glob('*.json')).write_text('{}')
        with pytest.raises(ValueError, match='checksum'):
            await registry.resolve(TARGET, incident_at=clock[0] + timedelta(seconds=2))
    asyncio.run(run())


@pytest.mark.parametrize('problem', ['unknown', 'traffic', 'outage', 'corrupt', 'stopped'])
def test_missing_stale_corrupt_or_unavailable_source_never_produces_approval_evidence(tmp_path, problem):
    async def run():
        review, source, _, clock = setup(tmp_path, missing=(5,) if problem == 'unknown' else (),
                                        no_traffic=(5,) if problem == 'traffic' else ())
        opened = await review.open()
        clock[0] += timedelta(minutes=5, seconds=38)
        if problem == 'stopped':
            clock[0] += timedelta(minutes=1)
        if problem == 'corrupt':
            next((source.store.root / 'raw').glob('*.json')).write_text('{}')
            with pytest.raises(ValueError, match='checksum'):
                await review.complete(opened['review_ref'])
        elif problem == 'outage':
            async def unavailable(*args):
                raise ConnectionError('fixture DB unavailable')
            source.next = unavailable
            with pytest.raises(ConnectionError):
                await review.complete(opened['review_ref'])
        else:
            result = await review.complete(opened['review_ref'])
            assert result['status'] in ('PENDING', 'INCONCLUSIVE') and 'evidence_ref' not in result
        assert not any('evidence_version' in review.store.read('artifact://manifests/' + p.name)
                       for p in (review.store.root / 'manifests').glob('*.json'))
    asyncio.run(run())


@pytest.mark.parametrize('problem', ['generation', 'pod', 'runtime-image', 'container', 'target', 'image', 'not-ready',
                                     'source', 'policy', 'environment', 'expired', 'observer', 'forged-card'])
def test_changed_rollout_source_expired_review_and_forged_cards_fail_closed(tmp_path, problem):
    async def run():
        review, source, kube, clock = setup(tmp_path)
        opened = await review.open()
        clock[0] += timedelta(minutes=5, seconds=38)
        if problem == 'generation': kube.value = replace(kube.value, generation=kube.value.generation + 1)
        if problem == 'pod': kube.value = replace(kube.value, pods=(replace(kube.value.pods[0], uid='replacement'),))
        if problem == 'runtime-image': kube.value = replace(kube.value, pods=(replace(kube.value.pods[0], image_id='sha256:'+'f'*64),))
        if problem == 'container': kube.value = replace(kube.value, container_index=2)
        if problem == 'target': kube.value = replace(kube.value, target=replace(TARGET, deployment_uid='recreated'))
        if problem == 'image': kube.value = replace(kube.value, image='registry/payment:latest')
        if problem == 'not-ready': kube.value = replace(kube.value, ready_replicas=0)
        if problem == 'source': source.stream_id = 'f' * 64
        if problem == 'policy': source.policy = {**source.policy, 'minimum_requests': 1}
        if problem == 'environment': review.environment = 'other'
        if problem == 'expired': clock[0] += timedelta(seconds=263)
        if problem == 'observer': kube.allowed = False
        if problem == 'forged-card':
            card = opened['review'];card['known_good_approved'] = True
            opened['review_ref'] = review.store.write('manifests', card)
        with pytest.raises((Blocked, ValueError)):
            await review.complete(opened['review_ref'])
    asyncio.run(run())


def test_source_failure_retry_restarts_verification_without_saved_healthy_streak(tmp_path):
    async def run():
        review, source, _, clock = setup(tmp_path)
        opened = await review.open();clock[0] += timedelta(minutes=5, seconds=38)
        original = source.next
        async def broken(checkpoint=None):
            if checkpoint is not None:
                raise ConnectionError('fixture source disconnected')
            return await original(checkpoint)
        source.next = broken
        with pytest.raises(ConnectionError): await review.complete(opened['review_ref'])
        source.next = original
        assert (await review.complete(opened['review_ref']))['status'] == 'AWAITING_OPERATOR_REVIEW'
    asyncio.run(run())


def test_final_clock_must_enclose_health_and_not_backdate_advanced_watermark(tmp_path):
    async def run():
        review, _, kube, clock = setup(tmp_path)
        opened = await review.open();clock[0] += timedelta(minutes=5, seconds=38)
        original = kube.observe; calls = []
        async def delayed():
            calls.append(1)
            if len(calls) == 2: clock[0] += timedelta(minutes=1)
            return await original()
        kube.observe = delayed
        result = await review.complete(opened['review_ref'])
        assert result['status'] == 'PENDING' and result['reason'] == 'health-watermark-advanced'
    asyncio.run(run())


def test_review_store_cannot_overlap_source_and_cli_has_no_approve_or_force_mode(tmp_path):
    from scripts.review_payment_image import main
    import sys
    from unittest.mock import patch
    review, source, kube, _ = setup(tmp_path)
    with pytest.raises(ValueError, match='separate'):
        ImageEvidenceReview(source, kube, ArtifactStore(source.store.root / 'nested'), TARGET)
    with patch.object(sys, 'argv', ['review_payment_image', '--help']), pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
