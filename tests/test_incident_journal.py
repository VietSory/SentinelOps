from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from sentinelops.incidents.domain import IncidentWrite
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.incident_journal import IncidentJournal, ACTOR

POLICY = json.loads(Path('configs/slo_health_policy.json').read_text())
RCA = json.loads(Path('configs/rca_policy.json').read_text())
TOPOLOGY = yaml.safe_load(Path('configs/topology.yaml').read_text())


def command(**changes):
    fields = {'payload': {'schema_version': '1.0', 'incident_id': 'fixture-journal',
        'detected_at': '2026-10-29T00:00:00Z', 'state': 'incident', 'severity': 'medium',
        'affected_services': ['payment-service'], 'service_signals': [], 'root_cause_ranking': [],
        'evidence': [{'id': 'fixture-fact', 'type': 'timeline', 'service': 'payment-service',
                      'observed_at': '2026-10-28T23:59:00Z', 'source_ref': 'fixture://source', 'fact': 'Software fixture'}]},
        'context': {'availability': 'UNKNOWN', 'recovery_source': None, 'source_refs': ['fixture://source'],
                    'config_refs': ['fixture://policy'], 'trigger_sources': [{'source': 'slo', 'signal_id': 'fixture-signal',
                        'emitted_at': '2026-10-29T00:00:00Z', 'source_ref': 'fixture://source'}]},
        'lifecycle_stage': 'OPEN', 'expected_revision': 0, 'idempotency_key': 'fixture-key',
        'actor': ACTOR, 'reason': 'software fixture; not a real source authentication test'}
    fields.update(changes)
    return IncidentWrite(**fields)


def journal(tmp_path, *, topology=TOPOLOGY):
    return IncidentJournal(None, SimpleNamespace(stream_id='a' * 64, policy=deepcopy(POLICY)),
                           ArtifactStore(tmp_path), topology, RCA, {'profile_version': 'software-fixture/v1'})


@pytest.mark.parametrize('problem', ['actor', 'model', 'manual', 'ranking', 'manual-recovery', 'action'])
def test_slo_journal_rejects_unsupported_actor_model_manual_or_action_projection(tmp_path, problem):
    item = command()
    if problem == 'actor': item = command(actor='untrusted-manual-client')
    elif problem == 'model': item.payload['service_signals'] = [{'service': 'payment-service', 'anomaly_score': .5}]
    elif problem == 'manual': item.context['trigger_sources'][0]['source'] = 'manual'
    elif problem == 'ranking': item.payload['root_cause_ranking'] = [{'service': 'payment-service', 'score': .5, 'evidence_refs': ['fixture-fact']}]
    elif problem == 'manual-recovery': item.context['recovery_source'] = 'manual'
    else:
        item = command(lifecycle_stage='MITIGATING')
        item.payload['state'] = 'recovering'
    with pytest.raises(ValueError): journal(tmp_path).freeze_command(item)
    assert list(tmp_path.rglob('*.json')) == []


def test_frozen_command_hash_and_body_cannot_diverge_if_caller_mutates_during_store_write(tmp_path, monkeypatch):
    target = journal(tmp_path)
    item = command()
    original = target.store.write
    def write(kind, body):
        ref = original(kind, body)
        item.payload['severity'] = 'critical'
        return ref
    monkeypatch.setattr(target.store, 'write', write)
    ref, fingerprint = target.freeze_command(item)
    saved = IncidentWrite(**target.store.read(ref))
    assert saved.payload['severity'] == 'medium' and item.payload['severity'] == 'critical'
    assert saved.fingerprint() == fingerprint


def test_consumer_requires_every_monitored_source_service_in_topology_before_registration(tmp_path):
    topology = deepcopy(TOPOLOGY)
    topology['dependencies'].pop('inventory-service')
    topology['dependencies']['checkout-service'].remove('inventory-service')
    with pytest.raises(ValueError, match='omits'): journal(tmp_path, topology=topology)
