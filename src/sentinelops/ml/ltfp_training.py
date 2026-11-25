"""Paired CPU development fitting on caller-admitted in-memory graphs only."""
from copy import deepcopy
import json
import math
from pathlib import Path
import time

import torch

from sentinelops.ml.ltfp_model import Forecast, export, load, metadata, predict
from sentinelops.telemetry.core import stable_id

SETTINGS = dict(seed=42, optimizer='Adam', learning_rate=.001, batch_size=32,
                steps=100, early_stopping=False, trials=1, dtype='float32')


def _tensors(graph, self_only):
    # Match predict: unavailable context contributes neither values nor attention.
    available = graph['target_available']
    values = [row if ready else [[0.] * 6 for _ in range(5)]
              for row, ready in zip(graph['values'], available)]
    mask = (torch.diag(torch.tensor(available, dtype=torch.bool)) if self_only
            else torch.tensor(graph['attention_mask'], dtype=torch.bool))
    return torch.tensor(values, dtype=torch.float32), mask


def fit_pair(samples, *, admission, expected_admission_sha256, output):
    """Caller certifies train admission and complete target neighborhoods.

    Admission graph hash preserves parent fit provenance. Each content-pinned
    sample carries its own validated inference topology; no neighbors are cut here.
    """
    fields = {'protocol_sha256', 'source_identity', 'config_sha256', 'graph_sha256',
              'train_split', 'calibration_split', 'role'}
    if (set(admission) != fields or admission['role'] != 'train'
        or any(not admission[key] for key in fields)
        or stable_id(admission) != expected_admission_sha256
        or admission['train_split'] == admission['calibration_split']):
        raise ValueError('invalid pinned train admission')
    ordered = sorted(deepcopy(samples), key=lambda sample: sample['record_id'])
    if not ordered or len({sample['record_id'] for sample in ordered}) != len(ordered):
        raise ValueError('unique train records required')
    identities = {name: metadata(config_sha256=admission['config_sha256'],
        graph_sha256=admission['graph_sha256'], self_only=variant, seed=42,
        train_split=admission['train_split'], calibration_split=admission['calibration_split'],
        weights_status='development-fitted') for name, variant in (('graph', False), ('self_only', True))}
    # Validate original W2 graph before deriving the self-only mask.
    with torch.random.fork_rng():
        torch.manual_seed(42)
        validator = Forecast()
    for sample in ordered:
        if set(sample) != {'record_id', 'graph', 'graph_sha256', 'labels', 'loss_mask'} or not isinstance(sample['record_id'], str) or not sample['record_id']:
            raise ValueError('invalid train sample')
        graph = sample['graph']
        if (stable_id(graph) != sample['graph_sha256'] or graph['source_identity'] != admission['source_identity']
            or graph['self_only'] is not False):
            raise ValueError('unadmitted source/graph')
        predict(validator, identities['graph'], graph, expected_graph_sha256=graph['graph_sha256'])
        nodes = graph['node_ids']
        if set(sample['labels']) != set(nodes) or set(sample['loss_mask']) != set(nodes):
            raise ValueError('labels/masks must align by node ID')
        for index, node in enumerate(nodes):
            label, selected = sample['labels'][node], sample['loss_mask'][node]
            if (type(selected) is not bool or (label is not None and (type(label) is not int or label not in (0, 1)))
                or selected and label is None):
                raise ValueError('UNKNOWN or invalid label in loss')
            if selected and not graph['target_available'][index]:
                raise ValueError('missing loss target')
        if not any(sample['loss_mask'].values()):
            raise ValueError('sample has no eligible loss targets')
    pin = stable_id({'admission': admission, 'samples': ordered, 'settings': SETTINGS})
    path = Path(output)
    if path.exists():
        receipt = json.loads((path / 'receipt.json').read_bytes())
        if (receipt['input_sha256'] != pin or receipt['settings'] != SETTINGS
            or receipt['admission_sha256'] != expected_admission_sha256
            or receipt['schema_version'] != 'ltfp-paired-fit/development-v1'
            or set(receipt['models']) != set(identities)):
            raise ValueError('completed fit identity mismatch')
        for name, identity in identities.items():
            entry = receipt['models'][name]
            if (entry['identity'] != identity or entry['steps'] != 100 or len(entry['losses']) != 100
                or any(not math.isfinite(value) for value in entry['losses'])):
                raise ValueError('completed model identity mismatch')
            load(path / name, metadata_sha256=entry['metadata_sha256'], expected_identity=identity)
        return receipt
    path.mkdir(parents=True, exist_ok=False)
    result = dict(schema_version='ltfp-paired-fit/development-v1', input_sha256=pin,
                  admission_sha256=expected_admission_sha256, settings=dict(SETTINGS), models={})
    # Restore caller RNG/thread settings; single-thread CPU gives repeatable tests/fits.
    previous_threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        for name, identity in identities.items():
            started = time.perf_counter()
            with torch.random.fork_rng():
                torch.manual_seed(42)
                model = Forecast()
                initial = {key: value.clone() for key, value in model.state_dict().items()}
                optimizer = torch.optim.Adam(model.parameters(), lr=.001)
                losses = []
                model.train()
                for step in range(100):
                    optimizer.zero_grad()
                    total = 0
                    count = 0
                    for offset in range(32):
                        sample = ordered[(step * 32 + offset) % len(ordered)]
                        graph = sample['graph']
                        logits = model(*_tensors(graph, identity['self_only']))
                        selected = [i for i, node in enumerate(graph['node_ids']) if sample['loss_mask'][node]]
                        labels = torch.tensor([sample['labels'][graph['node_ids'][i]] for i in selected], dtype=torch.float32)
                        total = total + torch.nn.functional.binary_cross_entropy_with_logits(logits[selected], labels, reduction='sum')
                        count += len(selected)
                    loss = total / count
                    if not torch.isfinite(loss):
                        raise ValueError('nonfinite training loss')
                    loss.backward()
                    optimizer.step()
                    losses.append(float(loss.detach()))
                if not any(not torch.equal(initial[key], value) for key, value in model.state_dict().items()):
                    raise ValueError('training did not update weights')
                for sample in ordered:
                    graph = deepcopy(sample['graph'])
                    if identity['self_only']:
                        graph['self_only'] = True
                        graph['attention_mask'] = _tensors(graph, True)[1].to(torch.int).tolist()
                        graph['mask_sha256'] = stable_id({'node_ids': graph['node_ids'], 'mask': graph['attention_mask']})
                    predict(model, identity, graph, expected_graph_sha256=graph['graph_sha256'])
                digest = export(model, identity, path / name)
                result['models'][name] = dict(identity=identity, metadata_sha256=digest,
                    steps=len(losses), losses=losses, elapsed_seconds=time.perf_counter()-started)
        (path / 'receipt.json').write_text(json.dumps(result, sort_keys=True, allow_nan=False))
    finally:
        torch.set_num_threads(previous_threads)
    return result
