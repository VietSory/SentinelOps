"""Untrained/development LTFP architecture; incompatible with pilot weights."""
import hashlib
import json
from pathlib import Path

import torch
from torch import nn

from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import stable_id

FEATURES = ('rt_log_level_rel', 'rt_slope_rel_per_min', 'rate_log_level_rel',
            'rate_slope_rel_per_min', 'rt_log_peak_rel', 'rt_log_baseline_to_breach')
VERSION = 'ltfp-six-feature-gru-attention/development-v1'


class Forecast(nn.Module):
    def __init__(self):
        super().__init__()
        self.temporal = nn.GRU(6, 8, batch_first=True)
        self.key = nn.Linear(8, 8, bias=False)
        self.query = nn.Linear(8, 8, bias=False)
        self.risk = nn.Linear(16, 1)

    def forward(self, values, attention_mask):
        encoded = self.temporal(values)[0][:, -1]
        attention = self.query(encoded) @ self.key(encoded).T / 8**.5
        # Zero attention for entirely masked rows without softmax(-inf,...,-inf).
        allowed = attention_mask.any(dim=1, keepdim=True)
        attention = attention.masked_fill(~attention_mask, -torch.inf)
        attention = torch.where(allowed, attention, torch.zeros_like(attention))
        weights = torch.softmax(attention, dim=-1) * attention_mask
        attended = weights @ encoded
        return self.risk(torch.cat((encoded, attended), dim=-1)).squeeze(-1)


def metadata(*, config_sha256, graph_sha256, self_only, seed, train_split, calibration_split,
             weights_status='untrained'):
    """Caller supplies split identities; metadata never implies a fitted artifact."""
    if (any(not isinstance(value, str) or not value for value in
            (config_sha256, graph_sha256, train_split, calibration_split))
        or type(self_only) is not bool or type(seed) is not int
        or weights_status not in ('untrained', 'development-fitted')):
        raise ValueError('invalid development model identity')
    return dict(architecture=VERSION, feature_names=list(FEATURES), sequence_length=5,
                dtype='float32', config_sha256=config_sha256, graph_sha256=graph_sha256,
                self_only=self_only, seed=seed, train_split=train_split,
                calibration_split=calibration_split, weights_status=weights_status)


def predict(model, identity, graph_input, *, expected_graph_sha256):
    """Consume W2 build_graph_input output; return node-ID probabilities or None."""
    expected = metadata(**{key: identity[key] for key in ('config_sha256', 'graph_sha256', 'self_only',
                         'seed', 'train_split', 'calibration_split', 'weights_status')})
    if identity != expected:
        raise ValueError('unsupported model architecture/feature identity')
    data = graph_input
    if not expected_graph_sha256 or data['graph_sha256'] != expected_graph_sha256:
        raise ValueError('inference graph differs from caller pin')
    for key in ('config_sha256', 'self_only', 'feature_names'):
        if data[key] != identity[key]:
            raise ValueError('graph/model identity mismatch')
    nodes, steps = data['node_ids'], data['step_minutes']
    n = len(nodes)
    if not n or len(set(nodes)) != n or any(not isinstance(node, str) or not node for node in nodes):
        raise ValueError('invalid graph node IDs')
    if (data['projection_version'] != 'ltfp-graph-input/development-v1'
        or len(steps) != 5 or any(type(step) is not int for step in steps)
        or steps != list(range(steps[0], steps[0] + 5))
        or not data['source_identity'] or steps[-1] * 60 > utc_time(data['as_of']).timestamp()
        or utc_time(data['topology']['observed_at']) > utc_time(data['as_of'])
        or stable_id(data['topology']) != data['graph_sha256']
        or sorted(nodes) != data['topology']['node_ids']):
        raise ValueError('invalid causal graph identity')
    validity = data['validity']
    if len(validity) != n or any(len(row) != 5 or any(type(v) is not bool for v in row) for row in validity):
        raise ValueError('invalid validity dimensions')
    available = [all(row) for row in validity]
    edges = {tuple(edge) for edge in data['topology']['directed_edges']}
    if any(len(edge) != 2 or any(node not in nodes for node in edge) for edge in edges):
        raise ValueError('invalid topology endpoints')
    pairs = edges | {(b, a) for a, b in edges}
    mask = [[int(available[i] and available[j] and (i == j or
             (not identity['self_only'] and (a, b) in pairs))) for j, b in enumerate(nodes)] for i, a in enumerate(nodes)]
    if (available != data['target_available'] or mask != data['attention_mask']
        or stable_id({'node_ids': nodes, 'mask': mask}) != data['mask_sha256']):
        raise ValueError('invalid availability/attention mask')
    if len(data['values']) != n or any(len(row) != 5 for row in data['values']):
        raise ValueError('invalid feature dimensions')
    values = []
    for i, row in enumerate(data['values']):
        sequence = []
        for j, vector in enumerate(row):
            if validity[i][j]:
                if not isinstance(vector, (list, tuple)) or len(vector) != 6:
                    raise ValueError('invalid feature dimensions')
                sequence.append(vector)
            else:
                sequence.append([0.] * 6)
        values.append(sequence if available[i] else [[0.] * 6 for _ in range(5)])
    tensor = torch.tensor(values, dtype=torch.float32, device='cpu')
    if not torch.isfinite(tensor).all():
        raise ValueError('nonfinite features')
    if any(p.device.type != 'cpu' or p.dtype != torch.float32 for p in model.parameters()):
        raise ValueError('model requires CPU float32')
    model.eval()
    with torch.no_grad():
        probabilities = torch.sigmoid(model(tensor, torch.tensor(mask, dtype=torch.bool))).tolist()
    if any(not 0 <= value <= 1 for value in probabilities):
        raise ValueError('nonfinite model probability')
    return {node: probability if available[i] else None for i, (node, probability) in enumerate(zip(nodes, probabilities))}


def export(model, identity, directory):
    """Write a new development directory; never overwrite a model artifact."""
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=False)
    torch.save(model.state_dict(), path / 'weights.pt')
    record = {'identity': identity, 'weights_sha256': hashlib.sha256((path / 'weights.pt').read_bytes()).hexdigest()}
    (path / 'metadata.json').write_text(json.dumps(record, sort_keys=True, allow_nan=False))
    return hashlib.sha256((path / 'metadata.json').read_bytes()).hexdigest()


def load(directory, *, metadata_sha256, expected_identity):
    path = Path(directory)
    raw = (path / 'metadata.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != metadata_sha256:
        raise ValueError('metadata checksum mismatch')
    record = json.loads(raw)
    if record['identity'] != expected_identity:
        raise ValueError('development model identity mismatch')
    weights = (path / 'weights.pt').read_bytes()
    if hashlib.sha256(weights).hexdigest() != record['weights_sha256']:
        raise ValueError('weights checksum mismatch')
    import io
    model = Forecast()
    model.load_state_dict(torch.load(io.BytesIO(weights), map_location='cpu', weights_only=True), strict=True)
    model.eval()
    return model
