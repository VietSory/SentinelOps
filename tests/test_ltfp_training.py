"""Synthetic fitting only; no admitted real data or model activation."""
from copy import deepcopy

import pytest
torch = pytest.importorskip("torch", reason="optional CPU research dependency; required in ltfp-cpu CI")

from sentinelops.ml.ltfp_training import _tensors, fit_pair
from sentinelops.ml.ltfp_model import load
from sentinelops.telemetry.core import stable_id
from test_ltfp_model import fixture


def inputs():
    _, identity, graph = fixture()
    admission = dict(protocol_sha256='fixture-protocol', source_identity=graph['source_identity'],
        config_sha256=identity['config_sha256'], graph_sha256=graph['graph_sha256'],
        train_split='fixture-train', calibration_split='fixture-cal', role='train')
    sample = dict(record_id='fixture-record', graph=graph, graph_sha256=stable_id(graph),
                  labels=dict(zip(graph['node_ids'], [0, 1, None])),
                  loss_mask=dict(zip(graph['node_ids'], [True, True, False])))
    return [sample], admission


def sparse_inputs():
    samples, admission = inputs()
    graph = samples[0]['graph']
    graph['node_ids'].append('d')
    graph['values'].append([[.3] * 6 for _ in range(5)])
    graph['values'][2][0] = None
    graph['validity'].append([True] * 5)
    graph['validity'][2][0] = False
    graph['target_available'] = [True, True, False, True]
    graph['topology']['node_ids'] = list(graph['node_ids'])
    graph['topology']['directed_edges'] = [('a', 'b'), ('a', 'c'), ('b', 'd')]
    graph['graph_sha256'] = stable_id(graph['topology'])
    graph['attention_mask'] = [[1, 1, 0, 0], [1, 1, 0, 1], [0, 0, 0, 0], [0, 1, 0, 1]]
    graph['mask_sha256'] = stable_id({'node_ids': graph['node_ids'], 'mask': graph['attention_mask']})
    admission['graph_sha256'] = graph['graph_sha256']
    full = dict(record_id='full', graph=graph, graph_sha256=stable_id(graph),
                labels=dict(a=0, b=None, c=None, d=None),
                loss_mask=dict(a=True, b=False, c=False, d=False))
    subset = deepcopy(full)
    subset['record_id'] = 'onehop'
    sub = subset['graph']
    for key in ('node_ids', 'values', 'validity', 'target_available'):
        sub[key] = sub[key][:3]
    sub['topology']['node_ids'] = list(sub['node_ids'])
    sub['topology']['directed_edges'] = [('a', 'b'), ('a', 'c')]
    sub['graph_sha256'] = stable_id(sub['topology'])
    sub['attention_mask'] = [row[:3] for row in sub['attention_mask'][:3]]
    sub['mask_sha256'] = stable_id({'node_ids': sub['node_ids'], 'mask': sub['attention_mask']})
    subset['graph_sha256'] = stable_id(sub)
    del subset['labels']['d'], subset['loss_mask']['d']
    return [full, subset], admission


@pytest.mark.parametrize('self_only', [False, True])
def test_full_and_all_onehop_selected_logit_and_loss_parity(self_only):
    samples, _ = sparse_inputs()
    model, _, _ = fixture()
    logits, losses = [], []
    for sample in samples:
        values, mask = _tensors(sample['graph'], self_only)
        assert values[2].count_nonzero() == 0
        assert not mask[2].any() and not mask[:, 2].any()
        selected = model(values, mask)[0:1]
        logits.append(selected)
        losses.append(torch.nn.functional.binary_cross_entropy_with_logits(selected, torch.tensor([0.])))
    torch.testing.assert_close(logits[0], logits[1], rtol=0, atol=1e-6)
    torch.testing.assert_close(losses[0], losses[1], rtol=0, atol=1e-6)


def test_paired_deterministic_update_export_and_completed_skip(tmp_path):
    samples, admission = sparse_inputs()
    before = deepcopy(samples)
    pin = stable_id(admission)
    first = fit_pair(samples, admission=admission, expected_admission_sha256=pin, output=tmp_path/'first')
    second = fit_pair(samples, admission=admission, expected_admission_sha256=pin, output=tmp_path/'second')
    assert fit_pair(samples, admission=admission, expected_admission_sha256=pin, output=tmp_path/'first') == first
    for name in ('graph', 'self_only'):
        a, b = first['models'][name], second['models'][name]
        assert a['identity']['graph_sha256'] == admission['graph_sha256']
        assert a['steps'] == 100 and a['losses'] == b['losses']
        assert all(torch.isfinite(torch.tensor(a['losses'])))
        model_a = load(tmp_path/'first'/name, metadata_sha256=a['metadata_sha256'], expected_identity=a['identity'])
        model_b = load(tmp_path/'second'/name, metadata_sha256=b['metadata_sha256'], expected_identity=b['identity'])
        assert all(torch.equal(value, model_b.state_dict()[key]) for key, value in model_a.state_dict().items())
    assert first['models']['graph']['losses'] != first['models']['self_only']['losses']
    assert samples == before
    changed = deepcopy(samples)
    changed[0]['labels']['a'] = 1
    with pytest.raises(ValueError, match='identity mismatch'):
        fit_pair(changed, admission=admission, expected_admission_sha256=pin, output=tmp_path/'first')


@pytest.mark.parametrize('bad', ['pin', 'source', 'graph-pin', 'topology-pin', 'mask', 'unknown', 'missing', 'split', 'config'])
def test_invalid_admission_or_loss_rejected_before_write(tmp_path, bad):
    samples, admission = inputs()
    if bad == 'source': samples[0]['graph']['source_identity'] = 'other'
    elif bad == 'graph-pin': samples[0]['graph_sha256'] = 'wrong'
    elif bad == 'topology-pin': samples[0]['graph']['graph_sha256'] = 'wrong'
    elif bad == 'mask': samples[0]['graph']['attention_mask'][0][1] = 0
    elif bad == 'unknown': samples[0]['labels']['a'] = None
    elif bad == 'missing':
        graph = samples[0]['graph']
        graph['validity'][0][0] = False
        graph['target_available'][0] = False
        graph['attention_mask'] = [[0, 0, 0], [0, 1, 0], [0, 0, 1]]
        graph['mask_sha256'] = stable_id({'node_ids': graph['node_ids'], 'mask': graph['attention_mask']})
    elif bad == 'split': admission['role'] = 'calibration'
    elif bad == 'config': admission['config_sha256'] = 'other'
    if bad != 'graph-pin': samples[0]['graph_sha256'] = stable_id(samples[0]['graph'])
    pin = 'wrong' if bad == 'pin' else stable_id(admission)
    with pytest.raises(ValueError): fit_pair(samples, admission=admission, expected_admission_sha256=pin, output=tmp_path/'out')
    assert not (tmp_path/'out').exists()
