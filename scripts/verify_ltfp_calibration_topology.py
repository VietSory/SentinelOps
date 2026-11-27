"""Verify development topology provenance for unseen calibration services."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

from scripts.audit_ltfp_graph_support import read_graph
from scripts.calibrate_ltfp_development import verify as verify_calibration
from sentinelops.ml.alibaba_training import split_of
from sentinelops.public_data.alibaba import file_sha256
from sentinelops.telemetry.core import stable_id, timestamp


def run(root):
    root = Path(root)
    verify_calibration(root)
    preflight = root / 'preflight'
    fit = json.loads((preflight / 'development-k16-result-v5.json').read_bytes())
    # Include graph contributors in the train identity boundary, not only targets.
    pins = fit['protocol']['graph_archives']
    paths = [root / 'input/callgraph' / name for name in sorted(pins)]
    training, train_pins = read_graph(paths)
    calibration, calibration_pins = read_graph(paths, role='calibration')
    if train_pins != pins or calibration_pins != pins:
        raise ValueError('graph archive pins differ from the fitted protocol')
    neighbors = defaultdict(set)
    outgoing = defaultdict(set)
    for up, down in set().union(*calibration.values()):
        neighbors[up].add(down)
        neighbors[down].add(up)
        outgoing[up].add(down)
    train_nodes = {row['service'] for row in fit['selected_records']}
    train_nodes.update(node for edges in training.values() for edge in edges for node in edge)
    verified = connected = 0
    last_graph_minute = max(calibration)
    topology_minute = last_graph_minute + 1
    observed_at = timestamp(datetime.fromtimestamp(topology_minute * 60, tz=timezone.utc))
    ledger = preflight / 'development-k16-calibration-v5/predictions.json'
    for row in json.loads(ledger.read_bytes()):
        service = row['service']
        nodes = {service} | neighbors[service]
        if (topology_minute >= row['minute'] - 4 or train_nodes & nodes
                or any(split_of(node) != 'calibration' for node in nodes)):
            raise ValueError('calibration topology crosses a training or time boundary')
        edges = sorted([up, down] for up in nodes for down in outgoing[up] if down in nodes)
        topology = {
            'identity': {'graph_archives': pins, 'scope': 'same-split-calibration-graph'},
            'observed_at': observed_at, 'node_ids': sorted(nodes), 'directed_edges': edges,
        }
        if stable_id(topology) != row['graph_sha256']:
            raise ValueError('calibration ledger graph digest differs from reconstructed topology')
        if row['scores']['ltfp_graph'] is not None:
            verified += 1
            connected += row['available_neighbors'] > 0
    if not verified or not connected:
        raise ValueError('no scored unseen calibration target with a usable graph edge')
    return {'scope': 'alibaba-proxy-development', 'status': 'verified',
            'source_clock': 'relative-minute-replay', 'availability': 'simulated-at-bucket-time',
            'last_graph_minute': last_graph_minute, 'topology_minute': topology_minute,
            'scored_unseen_targets': verified, 'scored_targets_with_neighbors': connected,
            'graph_archives': pins, 'calibration_ledger_sha256': file_sha256(ledger)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
