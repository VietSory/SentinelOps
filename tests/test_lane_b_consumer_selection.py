import asyncio
from collections import Counter
from copy import deepcopy
import json

import httpx
import pytest
import yaml

from scripts import check_lane_b_live as live, lane_b, lane_b_host
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health import HealthWorker
from sentinelops.workers.incident_evidence import NativeIncidentEvidence
from sentinelops.workers.incident_journal import IncidentJournal
from sentinelops.workers.health_source import HealthLedgerReader


def row(config='a', *, epoch=None):
    health_root, incident_root = lane_b.stream_roots(epoch)
    policy = json.loads((lane_b.PROJECT/'configs/slo_health_policy.json').read_text())
    worker = HealthWorker(None, ArtifactStore(health_root), policy,
                          endpoint='http://127.0.0.1:19090', environment='local', configuration_hash=config*64)
    profile = {'profile_version': 'native-incident-evidence/v1', 'compose_project': lane_b.NAME,
               'prometheus_endpoint': 'http://127.0.0.1:19090', 'jaeger_endpoint': 'http://127.0.0.1:26686'}
    journal = IncidentJournal(None, HealthLedgerReader(None, worker.store, worker.stream_id, policy),
        ArtifactStore(incident_root), yaml.safe_load((lane_b.PROJECT/'configs/topology.yaml').read_text()),
        json.loads((lane_b.PROJECT/'configs/rca_policy.json').read_text()), profile)
    return {'consumer_id': journal.consumer_id, 'health_stream_id': worker.stream_id,
            'identity': json.dumps(journal.identity), 'health_identity': json.dumps(worker.identity)}, worker.identity


def test_exact_current_consumer_with_multiple_historical_streams_no_mutation():
    old, _ = row('a'); current, identity = row('b')
    rows = [old, current]; before = deepcopy(rows)
    assert live.select_current_consumer(rows, identity) == current['consumer_id']
    assert rows == before
    assert live.select_current_consumer(list(reversed(rows)), identity) == current['consumer_id']


@pytest.mark.parametrize('problem', ['none', 'duplicate', 'wrong-id', 'different-source', 'different-policy', 'different-project', 'different-root'])
def test_no_ambiguous_or_foreign_consumer_can_be_selected(problem):
    current, identity = row('b'); old, _ = row('a'); rows = [old, current]
    if problem == 'none': rows = [old]
    elif problem == 'duplicate': rows.append(deepcopy(current))
    elif problem == 'wrong-id': current['consumer_id'] = '0'*64
    elif problem == 'different-source': current['health_identity'] = old['health_identity']
    else:
        value = json.loads(current['identity'])
        if problem == 'different-policy': value['health_policy_sha256'] = '0'*64
        elif problem == 'different-project': value['evidence_profile']['compose_project'] = 'sentinelops-other'
        else: value['artifact_root_hash'] = '0'*64
        current['identity'] = json.dumps(value)
    with pytest.raises(ValueError): live.select_current_consumer(rows, identity)


def test_release_epoch_keeps_legacy_history_and_launchers_bind_the_same_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(lane_b, 'ROOT', tmp_path)
    old, old_identity = row()
    new, identity = row(epoch='h1-review')
    assert identity['prometheus_configuration_hash'] == old_identity['prometheus_configuration_hash']
    assert old['health_stream_id'] != new['health_stream_id']
    assert live.select_current_consumer([old, new], old_identity) == old['consumer_id']
    assert live.select_current_consumer([old, new], identity, epoch='h1-review') == new['consumer_id']
    with pytest.raises(ValueError, match='identity differs'):
        live.select_current_consumer([old, new], identity)
    health, incidents = lane_b.stream_roots('h1-review')
    foreground = lane_b.launch_arguments('incidents', new['health_stream_id'], epoch='h1-review')
    hosted = lane_b_host.arguments('incidents', tmp_path, new['health_stream_id'], epoch='h1-review')
    for args in (foreground, hosted):
        assert args[args.index('--source-store') + 1] == str(health)
        assert args[args.index('--store') + 1] == str(incidents)
    for args in (lane_b.launch_arguments('health', epoch='h1-review'),
                 lane_b_host.arguments('health', tmp_path, None, epoch='h1-review')):
        assert args[args.index('--store') + 1] == str(health)
    with pytest.raises(ValueError, match='bounded'):
        lane_b.stream_roots('../legacy')
    (tmp_path / 'streams').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='owned'):
        lane_b.stream_roots('h1-review')


def test_b1_payment_payload_and_phase_gate_require_direct_success():
    def handler(request):
        assert request.url.path == '/v1/payments/charge'
        assert json.loads(request.content) == {'order_id': 'fixture-correlation', 'amount': 10.0, 'currency': 'USD'}
        return httpx.Response(200)

    async def request():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await live.business_request(client, 'payment-service', {'x-correlation-id': 'fixture-correlation'})

    assert asyncio.run(request()).status_code == 200
    counts = {service: Counter({200: 180}) for service in live.URLS}
    live.require_phase_traffic(counts)
    counts['payment-service'] = Counter({422: 180})
    with pytest.raises(ValueError, match='invalid business request to payment-service'):
        live.require_phase_traffic(counts)
    counts['payment-service'] = Counter({'transport_error': 180})
    with pytest.raises(ValueError, match='no successful business request to payment-service'):
        live.require_phase_traffic(counts)
