"""Bounded development protocol audit; declarations never authorize data execution."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
import re

from sentinelops.contracts import utc_time
from sentinelops.ml.alibaba_training import BASE_FEATURES, BREACH_FEATURE, split_of
from sentinelops.telemetry.core import stable_id


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _keys(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError('invalid protocol fields: ' + names)


def _path(root, name):
    if not isinstance(name, str):
        raise ValueError('unsafe relative path')
    path = Path(name)
    if not isinstance(name, str) or path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('unsafe relative path')
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('path escapes root')
    return resolved


def _ref(root, ref):
    _keys(ref, 'path sha256')
    if not isinstance(ref['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', ref['sha256']):
        raise ValueError('invalid byte SHA256')
    path = _path(root, ref['path'])
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ValueError('missing reference: ' + ref['path']) from exc
    if hashlib.sha256(content).hexdigest() != ref['sha256']:
        raise ValueError('missing or changed reference: ' + ref['path'])
    return content


def _json_ref(root, ref):
    value = json.loads(_ref(root, ref))
    _bytes(value)
    return value


def validate_worker_replay(replay):
    parity = replay['parity']
    if (replay['test_id'] != 'A07' or replay['status'] != 'passed'
            or replay['dataset_scope'] != 'alibaba-local-development-validation'
            or replay['run_id'] != parity['run_id']
            or parity['source_identity']['source_profile'] != 'prometheus-increase-60s-5s/v2'
            or parity['shape'] != [3, 5, 6] or not 0 <= parity['max_abs_error'] <= 1e-6
            or parity['late_batch_unavailable'] is not True
            or parity['exact_receipt_available'] is not True
            or parity['original_and_perturbed_suffix_invariant'] is not True):
        raise ValueError('A07 worker replay receipt did not establish parity')


def _overlapping_partitions(partitions):
    """Find any overlap between roles in the same source group."""
    latest_end = {}
    for unit, group, role, start, end in sorted(partitions, key=lambda item: item[3]):
        if end < start:
            continue
        other_role = 'calibration' if role == 'train' else 'train'
        other_end = latest_end.get((unit, group, other_role))
        if other_end is not None and other_end >= start:
            return True
        key = (unit, group, role)
        latest_end[key] = max(end, latest_end.get(key, end))
    return False


def audit_protocol(protocol, root):
    """Validate declared bytes and split dependencies; report independent lane gaps."""
    root = Path(root).resolve()
    _bytes(protocol)
    _keys(protocol, 'schema_version scope code data features graph training scoring calibration reporting checks')
    if protocol['schema_version'] != 'benchmark-protocol/development-v1' or protocol['scope'] != 'development':
        raise ValueError('development protocol required')
    code, data, features, graph = (protocol[key] for key in ('code', 'data', 'features', 'graph'))
    _keys(code, 'commit source_manifest')
    if not isinstance(code['commit'], str) or not re.fullmatch('[0-9a-f]{40}', code['commit']):
        raise ValueError('source commit required')
    source = _json_ref(root, code['source_manifest'])
    _keys(source, 'files')
    if not source['files']:
        raise ValueError('source file manifest empty')
    for ref in source['files']:
        _ref(root, ref)
    _keys(data, 'source manifest splits support')
    if data['source'] != 'alibaba-v2022':
        raise ValueError('Alibaba training source required')
    _json_ref(root, data['manifest'])
    splits = _json_ref(root, data['splits'])
    support = _json_ref(root, data['support'])
    _keys(splits, 'records')
    _keys(features, 'config ordered_features sequence_steps dtype')
    config = _json_ref(root, features['config'])
    if (features['ordered_features'] != list(BASE_FEATURES) + [BREACH_FEATURE]
            or features['sequence_steps'] != 5 or features['dtype'] != 'float32'
            or config['features'] != features['ordered_features'] or config['window_minutes'] != 5
            or config['baseline']['max_minutes'] != 60 or config['label']['horizon_seconds'] != 300
            or config['label']['confirmation_tail_minutes'] != 2):
        raise ValueError('feature dependency contract mismatch')
    _keys(graph, 'message_policy train_topology inference_topologies')
    if graph['message_policy'] != 'bidirectional-plus-self':
        raise ValueError('graph policy mismatch')
    topologies = [_json_ref(root, ref) for ref in [graph['train_topology'], *graph['inference_topologies']]]
    training = protocol['training']
    _keys(training, 'seed hidden_size attention_size optimizer learning_rate batch_size max_steps early_stopping trials')
    if any(training[key] != expected for key, expected in [('seed', 42), ('hidden_size', 8), ('attention_size', 8)]):
        raise ValueError('paired model settings mismatch')
    for key in ('batch_size', 'max_steps', 'trials'):
        if type(training[key]) is not int or training[key] <= 0:
            raise ValueError('positive training integer required')
    if (type(training['learning_rate']) not in (int, float) or training['learning_rate'] <= 0
            or not training['optimizer'] or not training['early_stopping']):
        raise ValueError('explicit training settings required')
    scoring = dict(oracle='latency-only', latency_ms=750, entry_buckets=2, horizon_seconds=300,
        step_seconds=60, feature_window_seconds=300, cutoff_policy='recorded-source-availability',
        episode_policy='workers.risk.EpisodeTracker', matching='earliest-unused-per-run-service',
        forecast_interval='[onset-300,onset)', qualified_lead_seconds=60, secondary_lead_seconds=180,
        fixed_slope={'threshold': .05, 'comparison': '>'})
    if protocol['scoring'] != scoring or protocol['calibration'] != dict(comparison='>=', budget=.01,
        minimum_scored_negative_windows=100, ties='preserve', fallback='nextafter-observed-max',
        cohort='eligible-scored-calibration-only'):
        raise ValueError('scoring/calibration contract mismatch')
    reporting = protocol['reporting']
    _keys(reporting, 'bootstrap_replicates seed unit interval primary lead_secondary required_tables')
    if reporting != dict(bootstrap_replicates=1000, seed=42, unit='whole-group', interval='percentile-linear',
        primary='delta-recall-lead-ge60', lead_secondary='paired-common-matched-events', required_tables=['forecast','detector']):
        raise ValueError('reporting contract mismatch')
    lanes = {lane: {'status': 'ready', 'reasons': []} for lane in ('a', 'b')}
    def gap(lane, reason):
        lanes[lane]['status'] = 'blocked'
        lanes[lane]['reasons'].append(reason)
    ids, assignments, partitions = set(), {}, []
    if not splits['records']:
        for lane in lanes: gap(lane, 'split records unavailable')
    for row in splits['records']:
        _keys(row, 'record_id service role split_unit group_id anchor_minute required_start required_end partition_start partition_end contributors directed_edges parent_split')
        role = row['parent_split'] if row['role'] == 'development' else row['role']
        if row['role'] not in ('train','calibration','development') or role not in ('train','calibration'):
            raise ValueError('evaluation/holdout or unknown role forbidden')
        if not all(isinstance(row[key], str) and row[key] for key in ('record_id','service','split_unit','group_id')) or row['record_id'] in ids:
            raise ValueError('unique explicit record/group/split identity required')
        ids.add(row['record_id'])
        partitions.append((row['split_unit'], row['group_id'], role, row['partition_start'], row['partition_end']))
        for node in [{'service':row['service'], 'role':role}, *row['contributors']]:
            _keys(node, 'service role')
            if node['role'] != role or split_of(node['service']) != role or assignments.get(node['service'], role) != role:
                gap('a', row['record_id'] + ': cross-split node/message')
            assignments[node['service']] = node['role']
        nodes = {row['service'], *(node['service'] for node in row['contributors'])}
        if any(len(edge) != 2 or any(node not in nodes for node in edge) for edge in row['directed_edges']):
            raise ValueError('edge outside explicit contributors')
        for key in ('anchor_minute','required_start','required_end','partition_start','partition_end'):
            if type(row[key]) is not int:
                raise ValueError('integer epoch minutes required')
        t = row['anchor_minute']
        if not (row['partition_start'] <= row['required_start'] <= t-68 and t+7 <= row['required_end'] <= row['partition_end']):
            gap('a', row['record_id'] + ': missing dependency coverage or purge')
    if _overlapping_partitions(partitions):
        for lane in lanes: gap(lane, 'overlapping source group partitions')
    if topologies[0].get('role') != 'train':
        raise ValueError('train topology requires train role')
    for topology in topologies:
        _keys(topology, 'identity observed_at directed_edges role')
        if not topology['identity'] or topology['role'] not in ('train','calibration'):
            raise ValueError('topology identity/role required')
        if splits['records'] and utc_time(topology['observed_at']).timestamp()/60 >= min(row['anchor_minute']-4 for row in splits['records']):
            gap('a', 'topology unavailable before first dependent anchor')
        applicable = [row for row in splits['records']
                      if (row['parent_split'] if row['role'] == 'development' else row['role']) == topology['role']]
        if not applicable:
            gap('a', 'topology has no applicable split records')
        topology_edges = {tuple(edge) for edge in topology['directed_edges']}
        outgoing = {}
        for edge in topology_edges:
            if len(edge) != 2:
                raise ValueError('topology edges must have two endpoints')
            outgoing.setdefault(edge[0], set()).add(edge)
        for row in applicable:
            nodes = {row['service'], *(node['service'] for node in row['contributors'])}
            expected = {edge for node in nodes for edge in outgoing.get(node, ()) if edge[1] in nodes}
            if {tuple(edge) for edge in row['directed_edges']} != expected:
                gap('a', row['record_id'] + ': record graph/topology mismatch')
        declared_edges = {tuple(edge) for row in applicable for edge in row['directed_edges']}
        if topology_edges - declared_edges:
            gap('a', 'topology edges lack split record binding')
        for up, down in topology['directed_edges']:
            if split_of(up) != topology['role'] or split_of(down) != topology['role']:
                gap('a', 'topology crosses service split')
    seen = set()
    for check in protocol['checks']:
        _keys(check, 'test_id evidence scope status')
        name = check['test_id']
        if name not in {f'A{i:02}' for i in range(1,10)} | {f'B{i:02}' for i in range(1,7)} or name in seen:
            raise ValueError('unknown/duplicate check')
        seen.add(name)
        if check['status'] not in ('passed', 'blocked', 'not-run') or check['scope'] not in ('real', 'fixture', 'counterfactual', 'input-only'):
            raise ValueError('unknown check status/scope')
        evidence = _json_ref(root, check['evidence'])
        if name == 'A07' and 'worker_replay' in evidence:
            validate_worker_replay(_json_ref(root, evidence.pop('worker_replay')))
        elif (name == 'A07' and check['status'] == 'passed'
              and support.get('lanes', {}).get('a', {}).get('dataset_scope') == 'alibaba-proxy-development'):
            gap('a', 'A07: verified worker replay receipt missing')
        _keys(evidence, 'test_id status scope source_manifest_sha256')
        if evidence != {**{key:check[key] for key in ('test_id','status','scope')}, 'source_manifest_sha256':code['source_manifest']['sha256']}:
            raise ValueError('check/source identity mismatch')
        lane = name[0].lower()
        software = {'A01','A02','A03','A04','A05','A06','A09','B01','B03','B04','B05'}
        accepted = {'real', 'fixture'} if name in software else {'real'}
        if check['status'] != 'passed' or check['scope'] not in accepted:
            gap(lane, name + ': required evidence scope not passed')
    for lane, count in [('a',9),('b',6)]:
        for i in range(1,count+1):
            name = f'{lane.upper()}{i:02}'
            if name not in seen: gap(lane, name + ': evidence missing')
        item = support.get('lanes', {}).get(lane)
        if not item or item.get('status') != 'ready' or item.get('scope') != 'real':
            gap(lane, 'real train/calibration support unavailable')
    reasons = [lane + ': ' + reason for lane, item in lanes.items() for reason in item['reasons']]
    return {'status':'blocked' if reasons else 'ready', 'protocol_sha256':stable_id(protocol), 'reasons':reasons, 'lanes':lanes}


def publish_freeze(path, content, *, conflict):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Root directories are trusted against hostile concurrent symlink replacement.
    # Publish only complete bytes; a hard link refuses replacement atomically.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.freeze-', delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError(conflict)
    finally:
        temporary.unlink(missing_ok=True)


def freeze_protocol(protocol, root):
    result = audit_protocol(protocol, root)
    if result['status'] != 'ready':
        return result
    wrapper = {'schema_version':'benchmark-protocol-freeze/development-v1', 'protocol':protocol,
               'protocol_content_sha256':result['protocol_sha256']}
    path = _path(Path(root).resolve(), 'freeze/protocol.json')
    publish_freeze(path, _bytes(wrapper), conflict='different protocol already frozen')
    return result


def verify_protocol(root):
    path = _path(Path(root).resolve(), 'freeze/protocol.json')
    try:
        content = path.read_bytes()
        wrapper = json.loads(content)
        _keys(wrapper, 'schema_version protocol protocol_content_sha256')
        if (wrapper['schema_version'] != 'benchmark-protocol-freeze/development-v1'
                or wrapper['protocol_content_sha256'] != stable_id(wrapper['protocol']) or content != _bytes(wrapper)):
            raise ValueError('changed protocol freeze')
        return audit_protocol(wrapper['protocol'], root)
    except FileNotFoundError as exc:
        raise ValueError('protocol freeze missing') from exc
