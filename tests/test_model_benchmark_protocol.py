from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random

import pytest

from sentinelops.ml.alibaba_training import load_config, split_of
from sentinelops.ml.model_benchmark_protocol import _overlapping_partitions, audit_protocol, freeze_protocol, verify_protocol


def fixture(root):
    def ref(name, value):
        path = root/name
        path.write_text(json.dumps(value, allow_nan=False))
        return {'path':name, 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    source = ref('source.json', {'files':[ref('code.json', {'code':'candidate'})]})
    service = next(f'MS_{i}' for i in range(100) if split_of(f'MS_{i}') == 'train')
    record = dict(record_id='r1',service=service,role='train',parent_split='train',split_unit='service',
        group_id='g1',anchor_minute=100,required_start=32,required_end=107,
        partition_start=32,partition_end=107,contributors=[],directed_edges=[])
    config = load_config()
    topology = ref('topology.json', dict(identity='topology1',observed_at='1970-01-01T00:00:00Z',
        directed_edges=[],role='train'))
    protocol = dict(schema_version='benchmark-protocol/development-v1',scope='development',
        code=dict(commit='a'*40,source_manifest=source),
        data=dict(source='alibaba-v2022',manifest=ref('data.json',{'source':'fixture'}),
            splits=ref('splits.json',{'records':[record]}),support=ref('support.json',
                {'lanes':{lane:{'status':'ready','scope':'real'} for lane in ('a','b')}})),
        features=dict(config=ref('features.json',config),ordered_features=config['features'],sequence_steps=5,dtype='float32'),
        graph=dict(message_policy='bidirectional-plus-self',train_topology=topology,inference_topologies=[topology]),
        training=dict(seed=42,hidden_size=8,attention_size=8,optimizer='adam',learning_rate=.001,
            batch_size=32,max_steps=1,early_stopping='none',trials=1),
        scoring=dict(oracle='latency-only',latency_ms=750,entry_buckets=2,horizon_seconds=300,step_seconds=60,
            feature_window_seconds=300,cutoff_policy='recorded-source-availability',episode_policy='workers.risk.EpisodeTracker',
            matching='earliest-unused-per-run-service',forecast_interval='[onset-300,onset)',qualified_lead_seconds=60,
            secondary_lead_seconds=180,fixed_slope={'threshold':.05,'comparison':'>'}),
        calibration=dict(comparison='>=',budget=.01,minimum_scored_negative_windows=100,ties='preserve',
            fallback='nextafter-observed-max',cohort='eligible-scored-calibration-only'),
        reporting=dict(bootstrap_replicates=1000,seed=42,unit='whole-group',interval='percentile-linear',
            primary='delta-recall-lead-ge60',lead_secondary='paired-common-matched-events',required_tables=['forecast','detector']),checks=[])
    for lane,count in [('A',9),('B',6)]:
        for i in range(1,count+1):
            name=f'{lane}{i:02}'
            evidence=dict(test_id=name,status='passed',scope='real',source_manifest_sha256=source['sha256'])
            protocol['checks'].append(dict(test_id=name,status='passed',scope='real',evidence=ref(name+'.json',evidence)))
    return protocol,ref


def test_deterministic_freeze_verify_retry_and_no_overwrite(tmp_path):
    protocol,_=fixture(tmp_path)
    assert audit_protocol(protocol,tmp_path)['status']=='ready'
    result=freeze_protocol(protocol,tmp_path)
    content=(tmp_path/'freeze/protocol.json').read_bytes()
    assert freeze_protocol(dict(reversed(list(protocol.items()))),tmp_path)==result
    assert verify_protocol(tmp_path)==result
    assert (tmp_path/'freeze/protocol.json').read_bytes()==content
    protocol['training']['max_steps']=2
    with pytest.raises(ValueError,match='already frozen'): freeze_protocol(protocol,tmp_path)
    assert (tmp_path/'freeze/protocol.json').read_bytes()==content


@pytest.mark.parametrize('field,value',[('partition_start',33),('partition_end',106),('required_start',33),('required_end',106)])
def test_exact_dependency_boundaries_and_purge(tmp_path,field,value):
    protocol,ref=fixture(tmp_path)
    path=tmp_path/'splits.json'; data=json.loads(path.read_text());data['records'][0][field]=value
    protocol['data']['splits']=ref('splits.json',data)
    result=freeze_protocol(protocol,tmp_path)
    assert result['lanes']['a']['status']=='blocked'
    assert result['lanes']['b']['status']=='ready'
    assert not (tmp_path/'freeze').exists()


def test_partition_overlap_sweep_matches_pairwise_reference():
    rng = random.Random(42)
    for _ in range(200):
        partitions = [(rng.choice(('a', 'b')), rng.choice(('g1', 'g2')),
                       rng.choice(('train', 'calibration')), rng.randrange(12), rng.randrange(12))
                      for _ in range(30)]
        expected = any(a[:2] == b[:2] and a[2] != b[2] and max(a[3], b[3]) <= min(a[4], b[4])
                       for index, a in enumerate(partitions) for b in partitions[index + 1:])
        assert _overlapping_partitions(partitions) == expected


def test_partition_overlap_scales_and_counts_touching_boundary():
    partitions = [('service', f'g{index}', 'train', 0, 179) for index in range(60000)]
    partitions += [('service', f'g{index}', 'calibration', 180, 359) for index in range(60000)]
    assert not _overlapping_partitions(partitions)
    partitions[-1] = ('service', 'g59999', 'calibration', 179, 359)
    assert _overlapping_partitions(partitions)


def test_partition_overlap_blocks_both_lanes(tmp_path):
    protocol, ref = fixture(tmp_path)
    data = json.loads((tmp_path / 'splits.json').read_text())
    service = next(f'MS_{i}' for i in range(100) if split_of(f'MS_{i}') == 'calibration')
    data['records'].append(dict(data['records'][0], record_id='r2', service=service,
        role='calibration', parent_split='calibration', anchor_minute=175,
        required_start=107, required_end=182, partition_start=107, partition_end=250))
    protocol['data']['splits'] = ref('splits.json', data)
    result = audit_protocol(protocol, tmp_path)
    for lane in ('a', 'b'):
        assert 'overlapping source group partitions' in result['lanes'][lane]['reasons']


def test_cross_split_neighbor_and_unknown_parent_rejected(tmp_path):
    protocol,ref=fixture(tmp_path)
    data=json.loads((tmp_path/'splits.json').read_text())
    service=next(f'MS_{i}' for i in range(100) if split_of(f'MS_{i}')=='calibration')
    data['records'][0]['contributors']=[{'service':service,'role':'calibration'}]
    protocol['data']['splits']=ref('splits.json',data)
    assert 'cross-split' in str(audit_protocol(protocol,tmp_path)['reasons'])
    data['records'][0].update(role='development',parent_split='holdout')
    protocol['data']['splits']=ref('splits.json',data)
    with pytest.raises(ValueError,match='holdout'): audit_protocol(protocol,tmp_path)


def test_topology_requires_all_directed_edges_between_record_nodes(tmp_path):
    protocol, ref = fixture(tmp_path)
    data = json.loads((tmp_path / 'splits.json').read_text())
    row = data['records'][0]
    other = next(f'MS_{i}' for i in range(100)
                 if split_of(f'MS_{i}') == 'train' and f'MS_{i}' != row['service'])
    edges = [[row['service'], other], [other, row['service']]]
    topology = json.loads((tmp_path / 'topology.json').read_text())
    topology['directed_edges'] = edges
    graph = ref('topology.json', topology)
    protocol['graph']['train_topology'] = graph
    protocol['graph']['inference_topologies'] = [graph]
    row['contributors'] = [{'service': other, 'role': 'train'}]
    row['directed_edges'] = edges
    protocol['data']['splits'] = ref('splits.json', data)
    assert audit_protocol(protocol, tmp_path)['lanes']['a']['status'] == 'ready'
    row['directed_edges'] = edges[:1]
    protocol['data']['splits'] = ref('splits.json', data)
    assert 'record graph/topology mismatch' in str(audit_protocol(protocol, tmp_path)['reasons'])


@pytest.mark.parametrize('change',['missing','hash','traversal','absolute','unknown','finite'])
def test_invalid_schema_refs_and_finite_json(tmp_path,change):
    protocol,_=fixture(tmp_path)
    if change=='missing': protocol['data']['manifest']['path']='absent'
    if change=='hash': protocol['data']['manifest']['sha256']='0'*64
    if change=='traversal': protocol['data']['manifest']['path']='../outside'
    if change=='absolute': protocol['data']['manifest']['path']='/tmp/outside'
    if change=='unknown': protocol['surprise']=1
    if change=='finite': protocol['training']['learning_rate']=float('inf')
    with pytest.raises(ValueError): audit_protocol(protocol,tmp_path)
    assert not (tmp_path/'freeze').exists()


def test_alibaba_a07_requires_a_pinned_worker_replay(tmp_path):
    protocol, ref = fixture(tmp_path)
    report = json.loads((tmp_path / protocol['data']['support']['path']).read_bytes())
    report['lanes']['a']['dataset_scope'] = 'alibaba-proxy-development'
    protocol['data']['support'] = ref('support-alibaba.json', report)
    assert audit_protocol(protocol, tmp_path)['lanes']['a']['reasons'] == [
        'A07: verified worker replay receipt missing']

    check = next(row for row in protocol['checks'] if row['test_id'] == 'A07')
    receipt = json.loads((tmp_path / check['evidence']['path']).read_bytes())
    replay = dict(test_id='A07', status='passed', dataset_scope='alibaba-local-development-validation',
                  run_id='run-fixture', parity=dict(run_id='run-fixture', shape=[3, 5, 6], max_abs_error=0,
                      source_identity={'source_profile': 'prometheus-increase-60s-5s/v2'},
                      late_batch_unavailable=True, exact_receipt_available=True,
                      original_and_perturbed_suffix_invariant=True))
    receipt['worker_replay'] = ref('worker-replay.json', replay)
    check['evidence'] = ref('a07-worker.json', receipt)
    assert audit_protocol(protocol, tmp_path)['lanes']['a']['status'] == 'ready'
    replay['parity']['late_batch_unavailable'] = False
    receipt['worker_replay'] = ref('worker-replay.json', replay)
    with pytest.raises(ValueError, match='missing or changed reference'):
        audit_protocol(protocol, tmp_path)
    check['evidence'] = ref('a07-worker.json', receipt)
    with pytest.raises(ValueError, match='did not establish parity'):
        audit_protocol(protocol, tmp_path)


def test_symlink_escape_source_drift_and_fixture_evidence(tmp_path):
    root=tmp_path/'root';root.mkdir();protocol,ref=fixture(root)
    outside=tmp_path/'outside';outside.write_text('{}');(root/'escape').symlink_to(outside)
    saved=deepcopy(protocol)
    protocol['data']['manifest']=dict(path='escape',sha256=hashlib.sha256(b'{}').hexdigest())
    with pytest.raises(ValueError,match='escapes'): audit_protocol(protocol,root)
    protocol=saved
    check=protocol['checks'][6];check['scope']='fixture'
    check['evidence']=ref('A07.json',dict(test_id='A07',status='passed',scope='fixture',
        source_manifest_sha256=protocol['code']['source_manifest']['sha256']))
    assert freeze_protocol(protocol,root)['lanes']['a']['status']=='blocked'
    assert not (root/'freeze').exists()
    (root/'code.json').write_text('changed')
    with pytest.raises(ValueError,match='changed'): audit_protocol(protocol,root)


def test_parse_uses_only_hashed_bytes(tmp_path,monkeypatch):
    protocol,ref=fixture(tmp_path)
    path=tmp_path/'splits.json'
    ready=path.read_bytes();data=json.loads(ready);data['records'][0]['required_start']=33
    protocol['data']['splits']=ref('splits.json',data)
    original=Path.read_bytes; calls=[]
    def mutate(self):
        content=original(self)
        if self==path:
            calls.append(content)
            self.write_bytes(ready)
        return content
    monkeypatch.setattr(Path,'read_bytes',mutate)
    assert audit_protocol(protocol,tmp_path)['status']=='blocked'
    assert len(calls)==1


def test_train_role_and_graph_record_binding(tmp_path):
    protocol,ref=fixture(tmp_path)
    topology=json.loads((tmp_path/'topology.json').read_text())
    topology['role']='calibration'
    protocol['graph']['train_topology']=ref('bad-topology.json',topology)
    with pytest.raises(ValueError,match='train role'):audit_protocol(protocol,tmp_path)
    protocol,ref=fixture(tmp_path)
    topology=json.loads((tmp_path/'topology.json').read_text())
    train=[f'MS_{i}' for i in range(100) if split_of(f'MS_{i}')=='train']
    topology['directed_edges']=[[train[0],train[1]]]
    top=ref('topology.json',topology)
    protocol['graph'].update(train_topology=top,inference_topologies=[top])
    assert 'binding' in str(audit_protocol(protocol,tmp_path)['reasons'])


def test_atomic_concurrent_identical_freeze_and_failed_publication(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import sentinelops.ml.model_benchmark_protocol as module
    protocol,_=fixture(tmp_path)
    original=module.os.link
    def fail(*args):raise OSError('publication failed')
    monkeypatch.setattr(module.os,'link',fail)
    with pytest.raises(OSError,match='publication failed'):freeze_protocol(protocol,tmp_path)
    assert not (tmp_path/'freeze/protocol.json').exists()
    assert not list((tmp_path/'freeze').iterdir())
    monkeypatch.setattr(module.os,'link',original)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:freeze_protocol(protocol,tmp_path),range(16)))
    assert all(item==results[0] for item in results)
    assert verify_protocol(tmp_path)==results[0]
    assert len(list((tmp_path/'freeze').iterdir()))==1


@pytest.mark.parametrize('name', ['A01','A06','A09','B01','B03','B05','A07','A08','B02','B06'])
def test_per_check_software_and_real_scopes(tmp_path,name):
    protocol,ref=fixture(tmp_path)
    check=next(c for c in protocol['checks'] if c['test_id']==name)
    check['scope']='fixture'
    check['evidence']=ref(name+'.json',dict(test_id=name,status='passed',scope='fixture',
        source_manifest_sha256=protocol['code']['source_manifest']['sha256']))
    result=audit_protocol(protocol,tmp_path)
    assert (result['status']=='ready') == (name not in ('A07','A08','B02','B06'))
