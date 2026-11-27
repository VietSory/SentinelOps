"""Synthetic fitting only; no admitted real data or model activation."""
from copy import deepcopy
import json

import pytest
torch = pytest.importorskip("torch", reason="optional CPU research dependency; required in ltfp-cpu CI")

from sentinelops.ml.ltfp_training import _tensors, _training_order, fit_pair
from sentinelops.ml import ltfp_training
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
    assert ltfp_training.protocol_training(first)['max_steps'] == 100
    assert ltfp_training.protocol_training(first)['early_stopping'] == 'none'
    assert samples == before
    changed = deepcopy(samples)
    changed[0]['labels']['a'] = 1
    with pytest.raises(ValueError, match='identity mismatch'):
        fit_pair(changed, admission=admission, expected_admission_sha256=pin, output=tmp_path/'first')
    legacy = deepcopy(first)
    legacy['settings'].pop('sampling')
    legacy['input_sha256'] = stable_id({'admission': admission,
        'samples': sorted(samples, key=lambda sample: sample['record_id']), 'settings': legacy['settings']})
    (tmp_path/'first'/'receipt.json').write_text(json.dumps(legacy))
    assert fit_pair(samples, admission=admission, expected_admission_sha256=pin, output=tmp_path/'first') == legacy


def test_interrupted_second_export_leaves_no_partial_output_and_can_retry(tmp_path, monkeypatch):
    samples, admission = inputs()
    pin = stable_id(admission)
    output = tmp_path / 'fit'
    original_export = ltfp_training.export

    def fail_second_export(model, identity, directory):
        if identity['self_only']:
            raise OSError('simulated interrupted export')
        return original_export(model, identity, directory)

    monkeypatch.setattr(ltfp_training, 'export', fail_second_export)
    with pytest.raises(OSError, match='interrupted export'):
        fit_pair(samples, admission=admission, expected_admission_sha256=pin, output=output)
    assert not output.exists()
    assert not list(tmp_path.iterdir())

    monkeypatch.setattr(ltfp_training, 'export', original_export)
    result = fit_pair(samples, admission=admission, expected_admission_sha256=pin, output=output)
    assert set(result['models']) == {'graph', 'self_only'}
    assert fit_pair(samples, admission=admission, expected_admission_sha256=pin, output=output) == result


def test_sampling_is_deterministic_and_does_not_prefer_sorted_record_ids():
    samples = [{'record_id': f'record-{index:04}'} for index in range(4000)]
    ordered = _training_order(samples)
    assert ordered == _training_order(list(reversed(samples)))
    trained_ids = {sample['record_id'] for sample in ordered[:3200]}
    assert len(trained_ids) == 3200
    assert any(record_id >= 'record-3200' for record_id in trained_ids)
    assert any(sample['record_id'] not in trained_ids for sample in samples[:3200])


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


def weighted_inputs(count=8):
    samples, admission = sparse_inputs()
    base = samples[0]
    result = []
    for index in range(count):
        sample = deepcopy(base)
        sample['record_id'] = f'r{index}'
        sample['graph']['values'][0][4] = [.1 * index] * 6
        sample['graph_sha256'] = stable_id(sample['graph'])
        sample['labels']['a'] = index % 2
        result.append(sample)
    return result, admission


def test_weighted_seed_mean_fit_is_deterministic_and_scores_mean_probability(tmp_path, monkeypatch):
    from sentinelops.ml.ltfp_model import SeedMean, predict
    monkeypatch.setitem(ltfp_training.WEIGHTED_SETTINGS, 'epochs', 2)
    monkeypatch.setitem(ltfp_training.WEIGHTED_SETTINGS, 'seeds', [42, 7])
    monkeypatch.setattr(ltfp_training, '_in_selection', lambda sample: sample['record_id'] in ('r0', 'r1', 'r2', 'r3'))
    samples, admission = weighted_inputs()
    pin = stable_id(admission)
    first = ltfp_training.fit_weighted_pair(samples, admission=admission, expected_admission_sha256=pin, output=tmp_path/'first')
    second = ltfp_training.fit_weighted_pair(samples, admission=admission, expected_admission_sha256=pin, output=tmp_path/'second')
    assert first['schema_version'] == 'ltfp-paired-fit/development-v2'
    assert first['fit_records'] == 4 and first['selection_records'] == 4 and first['positive_weight'] == 1.0
    assert ltfp_training.fit_weighted_pair(samples, admission=admission, expected_admission_sha256=pin,
                                           output=tmp_path/'first') == first
    graph = samples[0]['graph']
    for name in ('graph', 'self_only'):
        a, b = first['models'][name], second['models'][name]
        assert a['identity']['member_seeds'] == [42, 7] and [m['seed'] for m in a['members']] == [42, 7]
        assert [m['history'] for m in a['members']] == [m['history'] for m in b['members']]
        model = load(tmp_path/'first'/name, metadata_sha256=a['metadata_sha256'], expected_identity=a['identity'])
        other = load(tmp_path/'second'/name, metadata_sha256=b['metadata_sha256'], expected_identity=b['identity'])
        assert isinstance(model, SeedMean)
        assert all(torch.equal(value, other.state_dict()[key]) for key, value in model.state_dict().items())
        if name == 'graph':
            values, mask = _tensors(graph, False)
            with torch.no_grad():
                expected = torch.stack([torch.sigmoid(m(values, mask)) for m in model.members]).mean(0)[0]
            score = predict(model, a['identity'], graph, expected_graph_sha256=graph['graph_sha256'])['a']
            assert abs(score - float(expected)) < 1e-6
    assert ltfp_training.protocol_training(first) == dict(seed=42, hidden_size=8, attention_size=8,
        optimizer='Adam', learning_rate=.001, batch_size=32, max_steps=2,
        early_stopping='max-ap-on-train-service-hash-byte0-lt-51', trials=2)
    assert ltfp_training.repeated_fit_identity(first) == ltfp_training.repeated_fit_identity(second)
    changed = deepcopy(samples)
    changed[0]['labels']['a'] = 1
    with pytest.raises(ValueError, match='identity mismatch'):
        ltfp_training.fit_weighted_pair(changed, admission=admission, expected_admission_sha256=pin, output=tmp_path/'first')


def test_weighted_fit_requires_both_classes_in_fit_and_selection(tmp_path, monkeypatch):
    monkeypatch.setitem(ltfp_training.WEIGHTED_SETTINGS, 'epochs', 1)
    monkeypatch.setattr(ltfp_training, '_in_selection', lambda sample: sample['record_id'] in ('r0', 'r2'))
    samples, admission = weighted_inputs()
    with pytest.raises(ValueError, match='both classes'):
        ltfp_training.fit_weighted_pair(samples, admission=admission, expected_admission_sha256=stable_id(admission),
                                        output=tmp_path/'out')
    assert not (tmp_path/'out').exists()
