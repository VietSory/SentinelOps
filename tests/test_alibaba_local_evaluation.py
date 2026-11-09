"""ADR-016 epoch and source admission boundaries."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import json

import pytest

from sentinelops.experiments.local_campaign import planned_slots
from sentinelops.experiments.local_corpus import ALIBABA_PLAN, load_plan, recipe, register_session
from sentinelops.experiments.runner import execute
from sentinelops.ml.local_data import verify_alibaba_warmup
from sentinelops.telemetry.core import BUSINESS_ROUTES, timestamp
from sentinelops.telemetry.pipeline import process_captures
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2
from sentinelops.telemetry.store import ArtifactStore
from test_health_source import START, capture


def test_alibaba_epoch_is_evaluation_only_and_binds_candidate_before_docker(tmp_path, monkeypatch):
    plan = load_plan(ALIBABA_PLAN)
    slots = planned_slots(plan)
    assert len(slots) == 25 and all(role == 'development-validation' for role, _, _ in slots)
    assert {recipe(plan, *slot).scenario for slot in slots} == {'S0', 'S1', 'S3'}
    assert all(recipe(plan, *slot).warmup_seconds >= 1800 for slot in slots)
    store = ArtifactStore(tmp_path)
    from sentinelops.experiments import local_corpus
    monkeypatch.setattr(local_corpus, 'clean_source', lambda *_: 'a' * 40)
    monkeypatch.setattr(local_corpus, 'configure_compose', lambda *_: pytest.fail('reached Docker without a pin'))
    with pytest.raises(ValueError, match='candidate pinned'):
        register_session(store, plan, role='development-validation', ordinal=1, prepare=True)
    changed = tmp_path/'changed.json'
    changed.write_text(json.dumps({**plan, 'warmup_seconds': 300}))
    with pytest.raises(ValueError, match='committed evaluation-only plan'):
        load_plan(changed)


@pytest.mark.parametrize('changes', [{'split': 'train'}, {'warmup_seconds': 300}, {'all_entry_traffic': False}])
def test_v2_runner_rejects_training_or_incomplete_warmup_before_source(tmp_path, changes):
    plan = load_plan(ALIBABA_PLAN)
    config = replace(recipe(plan, 'development-validation', 1, 'payment-abrupt'), **changes)
    with pytest.raises(ValueError, match='30-minute warmup'):
        asyncio.run(execute(config, project=tmp_path, output=tmp_path/'output', source_profile=SOURCE_PROFILE_V2))
    assert not list(tmp_path.iterdir())


def test_alibaba_warmup_requires_every_independently_verified_minute(tmp_path):
    store = ArtifactStore(tmp_path)
    ends = [START + timedelta(minutes=minute) for minute in range(1, 31)]
    warmup_end = ends[-1] + timedelta(seconds=40)
    raw = [capture(service, end, errors=0, profile=SOURCE_PROFILE_V2)
           for service in BUSINESS_ROUTES for end in ends]
    normalized = process_captures(raw, store, default_profile=SOURCE_PROFILE_V2)
    observations = [store.read(ref) for ref in normalized['observation_refs']]
    oracle = {'services': {service: {'buckets': {
        str(int(end.timestamp()) - 60): {'availability': 'VERIFIED', 'breach': False} for end in ends
    }} for service in BUSINESS_ROUTES}}
    verify_alibaba_warmup(store, normalized, observations, oracle, timestamp(warmup_end), 30)
    oracle['services']['payment-service']['buckets'][str(int(ends[8].timestamp()) - 60)]['availability'] = 'UNKNOWN'
    with pytest.raises(ValueError, match='payment-service lacks 30 consecutive'):
        verify_alibaba_warmup(store, normalized, observations, oracle, timestamp(warmup_end), 30)
