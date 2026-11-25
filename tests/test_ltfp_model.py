"""Fixture-initialized weights test software only, not fitted model quality."""
from copy import deepcopy

import pytest
torch = pytest.importorskip("torch", reason="optional CPU research dependency; required in ltfp-cpu CI")

from sentinelops.ml.ltfp_model import FEATURES, Forecast, export, load, metadata, predict
from sentinelops.telemetry.core import stable_id


def fixture(self_only=False):
    torch.manual_seed(42)
    model = Forecast()
    nodes = ['a', 'b', 'c']
    topology = {'identity': 'fixture', 'observed_at': '2026-11-01T00:00:00Z',
                'node_ids': nodes, 'directed_edges': [('a', 'b')]}
    mask = [[1, int(not self_only), 0], [int(not self_only), 1, 0], [0, 0, 1]]
    data = dict(projection_version='ltfp-graph-input/development-v1', node_ids=nodes,
                step_minutes=list(range(29892961, 29892966)), as_of='2026-11-01T00:06:30Z',
                source_identity='fixture-source', feature_names=list(FEATURES), config_sha256='fixture-config',
                graph_sha256=stable_id(topology), topology=topology, self_only=self_only,
                attention_mask=mask, mask_sha256=stable_id({'node_ids': nodes, 'mask': mask}),
                validity=[[True]*5 for _ in nodes], target_available=[True]*3,
                values=torch.randn(3, 5, 6).tolist())
    # Epoch-minute clocks use the same units as W2's assembler.
    from sentinelops.contracts import utc_time
    anchor = int(utc_time('2026-11-01T00:06:00Z').timestamp()) // 60
    data['step_minutes'] = list(range(anchor - 4, anchor + 1))
    identity = metadata(config_sha256=data['config_sha256'], graph_sha256=data['graph_sha256'],
                        self_only=self_only, seed=42, train_split='fixture-train', calibration_split='fixture-cal')
    return model, identity, data


def test_roundtrip_determinism_permutation_and_no_mutation(tmp_path):
    model, identity, data = fixture()
    before = deepcopy(data)
    scores = predict(model, identity, data, expected_graph_sha256=identity['graph_sha256'])
    assert scores == predict(model, identity, data, expected_graph_sha256=identity['graph_sha256'])
    digest = export(model, identity, tmp_path / 'model')
    restored = load(tmp_path / 'model', metadata_sha256=digest, expected_identity=identity)
    assert scores == predict(restored, identity, data, expected_graph_sha256=identity['graph_sha256'])
    order = [2, 0, 1]
    shuffled = deepcopy(data)
    for key in ('node_ids', 'values', 'validity', 'target_available'):
        shuffled[key] = [data[key][i] for i in order]
    shuffled['attention_mask'] = [[data['attention_mask'][i][j] for j in order] for i in order]
    shuffled['mask_sha256'] = stable_id({'node_ids': shuffled['node_ids'], 'mask': shuffled['attention_mask']})
    assert predict(model, identity, shuffled, expected_graph_sha256=identity['graph_sha256']) == pytest.approx(scores, abs=1e-6)
    assert data == before
    changed = deepcopy(identity)
    changed['seed'] = 7
    with pytest.raises(ValueError): load(tmp_path / 'model', metadata_sha256=digest, expected_identity=changed)
    with pytest.raises(ValueError): load(tmp_path / 'model', metadata_sha256='wrong', expected_identity=identity)


def test_self_only_and_missing_neighbor():
    model, identity, data = fixture(True)
    expected = predict(model, identity, data, expected_graph_sha256=identity['graph_sha256'])['a']
    data['values'][1] = [[100.] * 6 for _ in range(5)]
    assert predict(model, identity, data, expected_graph_sha256=identity['graph_sha256'])['a'] == expected
    model, identity, data = fixture()
    data['validity'][1][0] = False
    data['target_available'][1] = False
    data['attention_mask'] = [[1, 0, 0], [0, 0, 0], [0, 0, 1]]
    data['mask_sha256'] = stable_id({'node_ids': data['node_ids'], 'mask': data['attention_mask']})
    expected = predict(model, identity, data, expected_graph_sha256=identity['graph_sha256'])
    assert expected['b'] is None
    data['values'][1] = [[float('nan')] * 6 for _ in range(5)]
    assert predict(model, identity, data, expected_graph_sha256=identity['graph_sha256']) == expected
    data['validity'] = [[False]*5 for _ in range(3)]
    data['target_available'] = [False]*3
    data['attention_mask'] = [[0]*3 for _ in range(3)]
    data['mask_sha256'] = stable_id({'node_ids': data['node_ids'], 'mask': data['attention_mask']})
    assert predict(model, identity, data, expected_graph_sha256=identity['graph_sha256']) == dict.fromkeys(data['node_ids'])


@pytest.mark.parametrize('field', ['feature_names', 'config_sha256', 'graph_sha256', 'mask_sha256', 'as_of', 'values', 'step_minutes'])
def test_identity_and_dimensions_rejected(field):
    model, identity, data = fixture()
    data[field] = {'feature_names': list(reversed(FEATURES)), 'config_sha256': 'other',
                   'graph_sha256': 'other', 'mask_sha256': 'other', 'as_of': '2026-10-31T00:00:00Z',
                   'values': [], 'step_minutes': [1, 2]}[field]
    with pytest.raises(ValueError): predict(model, identity, data, expected_graph_sha256=identity['graph_sha256'])


def test_unseen_topology_requires_caller_pin_and_preserves_training_identity():
    model, identity, data = fixture()
    original = deepcopy(identity)
    scores = predict(model, identity, data, expected_graph_sha256=data['graph_sha256'])
    renamed = deepcopy(data)
    renamed['node_ids'] = ['unseen-a', 'unseen-b', 'unseen-c']
    renamed['topology'] = {'identity': 'calibration-topology', 'observed_at': '2026-11-01T00:01:00Z',
                           'node_ids': renamed['node_ids'], 'directed_edges': [('unseen-a', 'unseen-b')]}
    renamed['graph_sha256'] = stable_id(renamed['topology'])
    renamed['mask_sha256'] = stable_id({'node_ids': renamed['node_ids'], 'mask': renamed['attention_mask']})
    result = predict(model, identity, renamed, expected_graph_sha256=renamed['graph_sha256'])
    assert list(result.values()) == list(scores.values())
    assert identity == original
    with pytest.raises(ValueError, match='caller pin'):
        predict(model, identity, renamed, expected_graph_sha256=identity['graph_sha256'])
    with pytest.raises(TypeError): predict(model, identity, renamed)
