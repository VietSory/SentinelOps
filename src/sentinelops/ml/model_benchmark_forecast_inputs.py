"""Verify sealed shared holdout identities before Lane A source replay."""
import json
import hashlib
from pathlib import Path
import re
import subprocess

import yaml

from sentinelops.contracts import utc_time
from sentinelops.ml.model_benchmark_holdout import (
    verify_candidate_freeze, verify_capture_inventory, verify_capture_registration, verify_frozen_capture_sources,
)
from sentinelops.ml.model_benchmark_protocol import _keys, _ref
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.store import ArtifactStore


def load_frozen_models(root, *, candidates_sha256):
    """Validate the four Lane A estimators before accessing sealed outcomes."""
    from sentinelops.ml.ltfp_model import load as load_ltfp
    from sentinelops.ml.models import load_bundle
    from xgboost import XGBClassifier

    root = Path(root).resolve()
    verify_candidate_freeze(root, candidates_sha256)
    candidate = json.loads(_ref(root, {'path': 'freeze/candidates.json', 'sha256': candidates_sha256}))
    lane = candidate['lanes']['a']
    fit = json.loads(_ref(root, {'path': 'preflight/development-k16-fit-v5/receipt.json',
                                 'sha256': lane['fit_sha256']}))
    bundle = root / 'data/bundle-20261113'
    manifest, _, risk = load_bundle(bundle, expected_manifest_sha256=candidate['baseline_bundle']['manifest_sha256'])
    config = manifest['provenance']['config']
    if (risk is None or manifest['model_version'] != candidate['baseline_bundle']['model_version']
            or stable_id(config) != manifest['provenance']['config_sha256']
            or config['features'] != manifest['ordered_features']):
        raise ValueError('frozen baseline bundle or feature config differs')
    models = {}
    for name, key, self_only in (('ltfp_graph', 'graph', False), ('ltfp_self_only', 'self_only', True)):
        entry = fit['models'][key]
        identity, pin = entry['identity'], lane['ltfp_metadata_sha256'][key]
        directory = root / 'preflight/development-k16-fit-v5' / key
        if (entry['metadata_sha256'] != pin or identity.get('weights_status') != 'development-fitted'
                or identity.get('self_only') is not self_only or identity.get('config_sha256') != stable_id(config)):
            raise ValueError('frozen LTFP identity differs from its fitted receipt')
        load_ltfp(directory, metadata_sha256=pin, expected_identity=identity)
        models[name] = {'directory': directory, 'metadata_sha256': pin, 'identity': identity, 'fitted_verified': True}
    xgb_dir = root / 'preflight/development-k16-xgboost-v5'
    xgb_bytes = (xgb_dir / 'manifest.json').read_bytes()
    xgb = json.loads(xgb_bytes)
    _ref(root, {'path': 'preflight/development-k16-xgboost-v5/risk.json', 'sha256': lane['xgboost_sha256']})
    if (xgb.get('scope') != 'development-train-only' or xgb.get('activation_allowed') is not False
            or xgb.get('model_sha256') != lane['xgboost_sha256']
            or xgb.get('paired_fit_input_sha256') != fit['input_sha256']
            or xgb.get('config_sha256') != stable_id(config)):
        raise ValueError('frozen XGBoost refit differs from the paired training input')
    model = XGBClassifier()
    model.load_model(xgb_dir / 'risk.json')
    return {'bundle': bundle, 'manifest_sha256': candidate['baseline_bundle']['manifest_sha256'],
            'config': config, 'models': models, 'xgboost_refit': {'directory': xgb_dir,
                'manifest_sha256': hashlib.sha256(xgb_bytes).hexdigest(), 'fit_input_sha256': fit['input_sha256']}}


def source_topology(run, *, project, capture_commit):
    """Use the capture revision's declared service graph, not post-run observations."""
    if (not isinstance(capture_commit, str) or not re.fullmatch('[0-9a-f]{40}', capture_commit)
            or run.get('source_commit') != capture_commit or run.get('dirty_worktree') is not False
            or run.get('environment') != 'local'):
        raise ValueError('topology requires the registered clean capture source')
    revision = capture_commit + ':configs/topology.yaml'
    try:
        data = subprocess.check_output(['git', 'show', revision], cwd=project, stderr=subprocess.DEVNULL)
        blob = subprocess.check_output(['git', 'rev-parse', revision], cwd=project,
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except subprocess.CalledProcessError as error:
        raise ValueError('capture source topology is unavailable') from error
    config = yaml.safe_load(data)
    if (not isinstance(config, dict) or not isinstance(config.get('dependencies'), dict)
            or not re.fullmatch('[0-9a-f]{40}', blob)):
        raise ValueError('capture source topology is malformed')
    dependencies = config['dependencies']
    if any(not isinstance(node, str) or not isinstance(targets, list)
           or any(not isinstance(target, str) for target in targets) for node, targets in dependencies.items()):
        raise ValueError('capture source topology has malformed dependencies')
    nodes = ['checkout-service', 'payment-service', 'inventory-service']
    if set(nodes) != set(BUSINESS_ROUTES) or not set(nodes) <= set(dependencies):
        raise ValueError('capture source topology omits a business service')
    edges = sorted({(node, target) for node, targets in dependencies.items() for target in targets})
    selected = [list(edge) for edge in edges if all(node in nodes for node in edge)]
    identity = {'source_commit': capture_commit, 'blob': blob, 'sha256': hashlib.sha256(data).hexdigest(),
                'scope': 'source-pinned declared graph; availability assigned at replay setup'}
    observed_at = timestamp(utc_time(run['started_at']))
    topology = {'identity': identity, 'observed_at': observed_at, 'directed_edges': selected}
    graph = {'identity': identity, 'observed_at': observed_at, 'node_ids': sorted(nodes), 'directed_edges': selected}
    return {'node_ids': nodes, 'topology': topology, 'graph_sha256': stable_id(graph),
            'excluded_edges': [list(edge) for edge in edges if not all(node in nodes for node in edge)]}


def load_sealed_inputs(root, *, candidates_sha256, registration_sha256, seal_sha256):
    """Replay every registered source only after both freezes and the complete seal pass."""
    root = Path(root).resolve()
    frozen = verify_candidate_freeze(root, candidates_sha256)
    registration = json.loads(_ref(root, {'path': 'capture/registration.json', 'sha256': registration_sha256}))
    registration = verify_capture_registration(root, registration_sha256=registration_sha256,
        matrix_sha256=registration['matrix_sha256'], candidates_sha256=candidates_sha256,
        capture_commit=registration['capture_commit'], frozen=frozen)
    seal = json.loads(_ref(root, {'path': 'capture/sealed.json', 'sha256': seal_sha256}))
    _keys(seal, 'schema_version registration_sha256 matrix_sha256 candidates_sha256 capture_commit groups '
                'slot_count capture_sealed activation_allowed')
    if (seal['schema_version'] != 'benchmark-holdout-seal/v1'
            or seal['registration_sha256'] != registration_sha256
            or seal['matrix_sha256'] != registration['matrix_sha256']
            or seal['candidates_sha256'] != candidates_sha256
            or seal['capture_commit'] != registration['capture_commit']
            or type(seal['slot_count']) is not int or seal['slot_count'] != 35
            or seal['capture_sealed'] is not True or seal['activation_allowed'] is not False):
        raise ValueError('holdout seal differs from the frozen capture design')
    matrix = json.loads((root / 'capture/matrix.json').read_bytes())
    if stable_id(matrix) != registration['matrix_sha256'] or len(matrix['groups']) != 5:
        raise ValueError('holdout matrix differs from its registration')
    groups = matrix['groups']
    if (not isinstance(seal['groups'], list) or len(seal['groups']) != len(groups)
            or any(not isinstance(item, dict) for item in seal['groups'])
            or [item.get('group_id') for item in seal['groups']] != [group['group_id'] for group in groups]):
        raise ValueError('holdout seal does not cover the registered groups in order')
    receipts = {}
    for group, pin in zip(groups, seal['groups']):
        _keys(pin, 'group_id receipt_sha256')
        group_id = group['group_id']
        receipts[group_id] = json.loads(_ref(root, {'path': f'capture/groups/{group_id}/receipt.json',
                                                     'sha256': pin['receipt_sha256']}))
    inventory = verify_capture_inventory(matrix, receipts, matrix_sha256=registration['matrix_sha256'],
                                         candidates_sha256=candidates_sha256)
    models = load_frozen_models(root, candidates_sha256=candidates_sha256)
    from sentinelops.ml.model_benchmark_forecast import thresholds_from_freeze
    thresholds = thresholds_from_freeze(root, candidates_sha256)
    slots = {slot['slot_id']: slot for group in groups for slot in group['slots']}
    paths = {row['slot_id']: root / 'capture/groups' / group_id / 'slots' / row['slot_id'] /
             'runs' / row['run_id'] / 'run.json' for group_id, rows in inventory.items() for row in rows}
    verified = verify_frozen_capture_sources(root, matrix, receipts, paths,
        matrix_sha256=registration['matrix_sha256'], candidates_sha256=candidates_sha256,
        capture_commit=registration['capture_commit'], registration_sha256=registration_sha256,
        certificate_store=ArtifactStore(root))
    ordered = [(group_id, row) for group_id, rows in inventory.items() for row in rows]
    if len(verified) != len(ordered) or len(verified) != seal['slot_count']:
        raise ValueError('verified holdout source count differs from its seal')
    cases = []
    for (group_id, row), source in zip(ordered, verified):
        if source['group_id'] != group_id or source['run_id'] != row['run_id']:
            raise ValueError('verified source identity differs from the sealed inventory')
        slot = slots[row['slot_id']]
        cases.append({**source, 'slot_id': row['slot_id'], 'kind': slot['kind'],
                      'run_path': str(paths[row['slot_id']])})
    return {'schema_version': 'benchmark-forecast-inputs/sealed-v1', 'candidates_sha256': candidates_sha256,
            'registration_sha256': registration_sha256, 'seal_sha256': seal_sha256,
            'capture_commit': registration['capture_commit'], 'matrix_sha256': registration['matrix_sha256'],
            'models': models, 'thresholds': thresholds, 'cases': cases}
