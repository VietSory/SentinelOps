"""Raw-codec software fixtures; no Kubernetes action or RCA acceptance claim."""
import asyncio
from copy import deepcopy
from datetime import timedelta

import pytest

from sentinelops.contracts import utc_time
from sentinelops.incidents.health import advance_health
from sentinelops.remediation.verification import verify_post_action_health
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import SOURCE_PROFILE
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health import HealthWorker
from sentinelops.workers.health_source import verify_health_batch
from test_health_source import POLICY, START, capture


class RawLedgerFixture:
    """Exercise the actual source verifier, not a boolean 'verified' test stub."""
    def __init__(self, root, errors, *, missing=(), no_traffic=(), late=()):
        self.store = ArtifactStore(root)
        self.policy = deepcopy(POLICY)
        worker = HealthWorker(None, self.store, self.policy, endpoint='http://localhost:9090',
                              environment='fixture', configuration_hash='a' * 64)
        self.stream_id = worker.stream_id
        self.rows, self.signals = [], []
        previous, captures, previous_ref = {}, [], None
        for index, error_count in enumerate(errors):
            end = START + timedelta(minutes=index)
            evaluated = end + timedelta(seconds=37)
            current = [capture(service, end, errors=error_count if service == 'payment-service' else 0)
                       for service in sorted(BUSINESS_ROUTES)]
            payment = next(raw for raw in current if raw['service'] == 'payment-service')
            if index in missing: payment['queries'].pop('buckets')
            if index in no_traffic:
                for key in ('requests', 'errors'):
                    for item in payment['queries'][key]['response']['data']['result']:
                        item['value'][1] = '0'
                for item in payment['queries']['buckets']['response']['data']['result']:
                    item['value'][1] = '0'
            refs = sorted(self.store.write('raw', raw) for raw in current)
            captures.extend(current)
            # Match the worker's five-bucket normalization history.
            norm = process_captures([raw for raw in captures
                if end - timedelta(minutes=4) <= utc_time(raw['bucket_end']) <= end],
                self.store, environment='fixture')
            observations = {o['service']: o for ref in norm['observation_refs']
                            if (o := self.store.read(ref))['bucket_end'] == timestamp(end)}
            states, signal_refs, records = {}, [], []
            for service in sorted(BUSINESS_ROUTES):
                signal = advance_health(previous.get(service), observations.get(service), service=service,
                    environment='fixture', source_profile=SOURCE_PROFILE, bucket_end=end,
                    evaluated_at=evaluated, policy=self.policy)
                states[service] = signal
                ref = self.store.write('signals', signal)
                signal_refs.append(ref)
                records.append({'stream_id':self.stream_id, 'bucket_end':end, 'service':service,
                                'signal_id':signal['signal_id'], 'signal_ref':ref, 'payload':signal})
            batch = {'worker_version':'continuous-health/v1', 'stream_id':self.stream_id,
                'identity':worker.identity, 'bucket_end':timestamp(end), 'evaluated_at':timestamp(evaluated),
                'previous_batch_ref':previous_ref, 'current_raw_refs':refs,
                'normalization_manifest_ref':norm['manifest_ref'], 'signal_refs':sorted(signal_refs),
                'source_status':'queried', 'model_outputs_available':False, 'actions_allowed':False}
            previous_ref = self.store.write('manifests', batch)
            self.rows.append({'stream_id':self.stream_id, 'bucket_end':end, 'manifest_ref':previous_ref,
                'evaluated_at':evaluated, 'recorded_at':evaluated + timedelta(seconds=1 if index not in late else 600)})
            self.signals.append(records)
            previous = states
        self.stream = {'stream_id':self.stream_id, 'identity':worker.identity, 'initial_bucket_end':START,
                      'last_bucket_end':self.rows[-1]['bucket_end'], 'last_batch_ref':previous_ref}

    async def next(self, checkpoint=None):
        index = 0 if checkpoint is None else next(i + 1 for i, row in enumerate(self.rows)
            if row['manifest_ref'] == checkpoint['last_batch_ref'])
        if index == len(self.rows): return None
        row = self.rows[index]
        verified = verify_health_batch(self.store, self.stream, row, self.signals[index],
            self.rows[max(0, index-4):index], checkpoint['service_states'] if checkpoint else None,
            policy=self.policy)
        return {**verified, 'accepted_row':row, 'source_stream':self.stream}


def check(source, *, effective=START, minutes=6, **kwargs):
    return asyncio.run(verify_post_action_health(source, effective_at=effective,
        as_of=START + timedelta(minutes=minutes), expected_environment='fixture', **kwargs))


def test_three_whole_post_action_buckets_required_and_restart_reproduces(tmp_path):
    source = RawLedgerFixture(tmp_path, [0] * 5)
    pending = check(source, minutes=3)
    assert pending['status'] == 'pending' and len(pending['support']) == 2
    actual = check(source)
    assert actual == check(source)
    assert actual['status'] == 'telemetry_verified'
    assert [item['bucket_end'] for item in actual['support']] == [timestamp(START + timedelta(minutes=i)) for i in (1,2,3)]
    assert actual['recovery_accepted'] is actual['action_dispatch_authorized'] is False
    assert actual['deployment_identity_verified'] is False


def test_partial_bucket_and_pre_action_health_streak_are_excluded(tmp_path):
    source = RawLedgerFixture(tmp_path, [0] * 6)
    result = check(source, effective=START + timedelta(minutes=1, seconds=20), minutes=5)
    assert result['status'] == 'pending'
    assert [item['bucket_end'] for item in result['support']] == [timestamp(START + timedelta(minutes=i)) for i in (3,4)]


@pytest.mark.parametrize('problem', ['unknown', 'traffic', 'breach'])
def test_missing_traffic_unknown_and_breaches_reset_own_post_action_streak(tmp_path, problem):
    errors = [0,0,0,12 if problem == 'breach' else 0,0,0]
    source = RawLedgerFixture(tmp_path, errors, missing=(3,) if problem == 'unknown' else (),
                              no_traffic=(3,) if problem == 'traffic' else ())
    result = check(source, minutes=10)
    assert result['status'] == 'inconclusive' and len(result['support']) == 2
    assert result['reason'] == 'post-action-telemetry-unknown-or-breaching'


def test_late_receipt_cannot_retroactively_complete_timed_out_verification(tmp_path):
    source = RawLedgerFixture(tmp_path, [0] * 5, late=(3,))
    result = check(source, minutes=20)
    assert result['status'] == 'inconclusive' and len(result['support']) == 2


def test_no_history_and_scan_budget_never_claim_recovery(tmp_path):
    source = RawLedgerFixture(tmp_path, [0] * 5)
    result = check(source, max_batches=2)
    assert result['status'] == 'inconclusive' and result['reason'] == 'verified-history-budget-exhausted'
    with pytest.raises(ValueError, match='budget'): check(source, max_batches=True)
    result = check(source, effective=START + timedelta(minutes=8), minutes=20)
    assert result['status'] == 'inconclusive' and result['support'] == []


def test_foreign_environment_and_corrupt_raw_fail_closed(tmp_path):
    source = RawLedgerFixture(tmp_path, [0] * 5)
    with pytest.raises(ValueError, match='environment'):
        asyncio.run(verify_post_action_health(source, effective_at=START,
            as_of=START + timedelta(minutes=6), expected_environment='production'))
    ref = source.store.read(source.rows[1]['manifest_ref'])['current_raw_refs'][0]
    (source.store.root / ref.removeprefix('artifact://')).write_text('{}')
    with pytest.raises(ValueError, match='checksum'): check(source)


def test_source_outage_propagates_and_retry_restarts_without_streak_increment(tmp_path, monkeypatch):
    source = RawLedgerFixture(tmp_path, [0] * 5)
    original = source.next
    async def fail(checkpoint=None):
        if checkpoint and checkpoint['last_bucket_end'] == timestamp(START + timedelta(minutes=2)):
            raise ConnectionError('fixture database unavailable after two clear buckets')
        return await original(checkpoint)
    monkeypatch.setattr(source, 'next', fail)
    with pytest.raises(ConnectionError): check(source)
    monkeypatch.setattr(source, 'next', original)
    assert check(source)['status'] == 'telemetry_verified'


def test_duplicate_or_skipped_source_bucket_cannot_increment_persistence(tmp_path, monkeypatch):
    source = RawLedgerFixture(tmp_path, [0] * 5)
    original = source.next
    async def duplicate(checkpoint=None):
        return await original(None)
    monkeypatch.setattr(source, 'next', duplicate)
    with pytest.raises(ValueError, match='skipped, duplicated or reordered'): check(source)


def test_invalid_effectiveness_and_decision_clocks_are_rejected(tmp_path):
    source = RawLedgerFixture(tmp_path, [0])
    with pytest.raises(ValueError): check(source, effective=START + timedelta(minutes=7))
    with pytest.raises(ValueError): check(source, effective=START.replace(tzinfo=None))
