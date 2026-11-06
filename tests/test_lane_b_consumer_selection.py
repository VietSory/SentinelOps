from copy import deepcopy
import json

import pytest
import yaml

from scripts import check_lane_b_live as live, lane_b
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.health import HealthWorker
from sentinelops.workers.incident_evidence import NativeIncidentEvidence
from sentinelops.workers.incident_journal import IncidentJournal
from sentinelops.workers.health_source import HealthLedgerReader


def row(config='a'):
    policy = json.loads((lane_b.PROJECT/'configs/slo_health_policy.json').read_text())
    worker = HealthWorker(None, ArtifactStore(lane_b.ROOT/'health'), policy,
                          endpoint='http://127.0.0.1:19090', environment='local', configuration_hash=config*64)
    profile = {'profile_version': 'native-incident-evidence/v1', 'compose_project': lane_b.NAME,
               'prometheus_endpoint': 'http://127.0.0.1:19090', 'jaeger_endpoint': 'http://127.0.0.1:26686'}
    journal = IncidentJournal(None, HealthLedgerReader(None, worker.store, worker.stream_id, policy),
        ArtifactStore(lane_b.ROOT/'incidents'), yaml.safe_load((lane_b.PROJECT/'configs/topology.yaml').read_text()),
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
