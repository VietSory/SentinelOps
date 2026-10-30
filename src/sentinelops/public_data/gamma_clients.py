"""Quarantined GAMMA client captures: source integrity and format capability only."""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import zlib

from sentinelops.public_data.gamma import canonical_bytes, publish_bytes
from sentinelops.public_data.gamma_archive import archive_catalog
from sentinelops.public_data.policy import load_policy, policy_hash, require_training_source


MAX_FILE = 1_048_576
MAX_TOTAL = 8_388_608
MAX_LINES = 200_000
MAX_WIRE = 32_000_000
MAX_CALLS = 256
KINDS = ('compose', 'home', 'user')
SUFFIXES = ('.log', '.warm_up', '_latencies.txt', '_latencies.warm_up')
CASES = ('cpu_july27_60min_800_0', 'cpu_july28_800_0', 'cpu_july29_800_0', 'cpu_test_july24_800_0')


def digest(value: dict) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def client_plan(scope: dict, catalog: dict, groups_blob: bytes, splits_blob: bytes) -> list[dict]:
    """Validate the entire role/inventory snapshot before any client bytes/network."""
    policy = load_policy()
    require_training_source('gamma', policy)
    fields = {'scope_version', 'catalog_sha256', 'training_data_policy_hash', 'group_snapshot_sha256',
              'split_snapshot_sha256', 'experiments', 'files', 'training_ready', 'activation_allowed'}
    if (set(scope) != fields or scope['scope_version'] != 'gamma-client-captures/v1'
        or scope['experiments'] != list(CASES)
        or scope['training_ready'] is not False or scope['activation_allowed'] is not False
        or scope['training_data_policy_hash'] != policy_hash(policy)
        or catalog.get('training_data_policy_hash') != policy_hash(policy)
        or catalog.get('source_version') != 1 or catalog.get('version_id') != 7638732
        or digest(catalog) != scope['catalog_sha256']):
        raise ValueError('client scope/catalog differs from frozen GAMMA source policy')
    # Verify actual serialized snapshots, never a caller's self-declared digest.
    if (not isinstance(groups_blob, bytes) or not isinstance(splits_blob, bytes)
        or hashlib.sha256(groups_blob).hexdigest() != scope['group_snapshot_sha256']
        or hashlib.sha256(splits_blob).hexdigest() != scope['split_snapshot_sha256']):
        raise ValueError('client group/split snapshot identity changed')
    groups, splits = json.loads(groups_blob), json.loads(splits_blob)
    for case in CASES:
        gs = [g for g in groups['groups'] if case in g['experiments']]
        rs = [a for a in splits['assignments'] if case in a['experiments']]
        if (len(gs) != 1 or len(rs) != 1 or rs[0]['group_id'] != gs[0]['group_id']
            or rs[0]['split'] != 'quarantine' or gs[0]['admitted_cases'] != 0
            or gs[0]['final_holdout_eligible'] is not False):
            raise ValueError('client members must belong to whole quarantined non-holdout groups')
    expected = {f'raw_dataset/{case}/{kind}{suffix}' for case in CASES for kind in KINDS for suffix in SUFFIXES}
    files = scope['files']
    if (not isinstance(files, list) or len(files) != 48
        or any(not isinstance(f, dict) for f in files)
        or {f.get('name') for f in files} != expected):
        raise ValueError('client scope must contain exactly the 48 predeclared members')
    index = {f['name']: f for f in catalog['files']}
    for item in files:
        record = {k: v for k, v in item.items() if k != 'sha256'}
        if (record != index.get(item['name']) or item['directory'] is not False
            or type(item['size']) is not int or not 0 <= item['size'] <= MAX_FILE
            or not isinstance(item.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
            raise ValueError('client inventory member identity/budget differs from catalogue')
    if sum(f['size'] for f in files) > MAX_TOTAL:
        raise ValueError('client total expanded byte budget exceeded')
    return sorted(files, key=lambda f: f['name'])


def load_context(scope: Path, catalog: Path, groups: Path, splits: Path) -> tuple[dict, dict, bytes, bytes]:
    values = []
    for path, bound in ((scope, 128 * 1024), (catalog, MAX_WIRE), (groups, 256 * 1024), (splits, 256 * 1024)):
        with path.open('rb') as stream:
            blob = stream.read(bound + 1)
        if len(blob) > bound:
            raise ValueError('client context exceeds metadata byte budget')
        value = blob if path in (groups, splits) else json.loads(blob)
        values.append(value)
    client_plan(*values)
    return tuple(values)


def _target(root: Path, name: str) -> Path:
    target = root / name
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError('client cache symlinks refused')
    return target


def _read(root: Path, item: dict) -> bytes:
    path = _target(root, item['name'])
    with path.open('rb') as stream:
        blob = stream.read(item['size'] + 1)
    if (len(blob) != item['size'] or hashlib.sha256(blob).hexdigest() != item['sha256']
        or f'{zlib.crc32(blob):08x}' != item['crc32']):
        raise ValueError('client cache differs from frozen size/CRC32/project SHA256')
    return blob


class _BoundedClient:
    def __init__(self, client):
        self.client, self.calls, self.started = client, 0, time.monotonic()

    def stream(self, *args, **kwargs):
        if self.calls >= MAX_CALLS or time.monotonic() - self.started > 600:
            raise ValueError('client source request/time budget exceeded')
        self.calls += 1
        return self.client.stream(*args, **kwargs)


def download_clients(client, root: Path, scope: dict, catalog: dict, groups: bytes, splits: bytes) -> dict:
    plan = client_plan(scope, catalog, groups, splits)
    # Validate every existing target first; never repair a corrupt immutable cache.
    cached = set()
    for item in plan:
        if _target(root, item['name']).exists():
            _read(root, item)
            cached.add(item['name'])
    calls = wire = ranges = 0
    if len(cached) != len(plan):
        bounded = _BoundedClient(client)
        fresh, reader, archive = archive_catalog(bounded, max_bytes=MAX_WIRE)
        try:
            if fresh != catalog:
                raise ValueError('client archive differs from frozen catalogue')
            for item in plan:
                if item['name'] in cached:
                    continue
                info = archive.getinfo(item['name'])
                with archive.open(info) as member:
                    blob = member.read(MAX_FILE + 1)
                if (len(blob) != item['size'] or hashlib.sha256(blob).hexdigest() != item['sha256']
                    or f'{zlib.crc32(blob):08x}' != item['crc32']):
                    raise ValueError('downloaded client differs from frozen member identity')
                publish_bytes(_target(root, item['name']), blob)
            calls, wire, ranges = bounded.calls, reader.body_bytes, reader.requests
        finally:
            archive.close()
            reader.close()
    for item in plan:
        _read(root, item)
    return {'status': 'PASS', 'scope_sha256': digest(scope), 'files': len(plan),
            'expanded_bytes': sum(f['size'] for f in plan), 'cached_files': len(cached),
            'range_requests': ranges, 'archive_response_body_bytes': wire, 'metadata_and_range_calls': calls,
            'training_ready': False, 'activation_allowed': False,
            'hash_authority': 'catalog CRC32 and project-frozen SHA256; not publisher-supplied file SHA256'}


def _lines(blob: bytes) -> list[str]:
    if len(blob) > MAX_FILE:
        raise ValueError('client format exceeds file byte budget')
    text = blob.decode('utf-8')
    # Check before splitlines allocates potentially many tiny strings.
    if sum(1 for _ in re.finditer(r'\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]', text)) > MAX_LINES:
        raise ValueError('client format exceeds line budget')
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        raise ValueError('client format exceeds line budget')
    return lines


def summarize_log(blob: bytes) -> dict:
    lines = _lines(blob)
    summaries, histograms, sockets, non_success = [], [], [], []
    for line in lines:
        match = re.fullmatch(r'\s*([0-9]{1,16}) requests in ([0-9]{1,8}(?:\.[0-9]{1,9})?)(us|ms|s|m|h), [0-9.]+[A-Za-z]+ read\s*', line)
        if match:
            count, duration, unit = match.groups()
            seconds = Decimal(duration) * {'us': Decimal('.000001'), 'ms': Decimal('.001'),
                                          's': Decimal(1), 'm': Decimal(60), 'h': Decimal(3600)}[unit]
            summaries.append({'requests': int(count), 'duration_value': duration,
                              'duration_unit': unit, 'duration_seconds': str(seconds)})
        match = re.fullmatch(r'\s*#\[Max\s*=\s*[0-9.]+, Total count\s*=\s*([0-9]{1,16})\]\s*', line)
        if match:
            histograms.append(int(match[1]))
        match = re.fullmatch(r'\s*Socket errors: connect ([0-9]+), read ([0-9]+), write ([0-9]+), timeout ([0-9]+)\s*', line)
        if match:
            sockets.append(dict(zip(('connect', 'read', 'write', 'timeout'), map(int, match.groups()))))
        match = re.fullmatch(r'\s*Non-2xx or 3xx responses: ([0-9]+)\s*', line)
        if match:
            non_success.append(int(match[1]))
    if len(summaries) != 1 or any(len(v) > 1 for v in (histograms, sockets, non_success)):
        raise ValueError('client summary missing, duplicated or ambiguous')
    return {'lines': len(lines), 'summary': summaries[0],
            'recorded_histogram_count': histograms[0] if histograms else None,
            'socket_errors': sockets[0] if sockets else None,
            'non_2xx_3xx_responses': non_success[0] if non_success else None,
            'http_5xx_count': None, 'event_clock_available': False,
            'per_bucket_request_denominator_available': False,
            'histogram_scope': 'whole printed run; not a 60-second bucket',
            'hdr_recorded_latency_heading': any('HdrHistogram - Recorded Latency' in line for line in lines)}


def summarize_integers(blob: bytes) -> dict:
    lines = _lines(blob)
    if not lines or any(not re.fullmatch('[0-9]{1,20}', line) or int(line) > 2**64 - 1 for line in lines):
        raise ValueError('client integer stream empty or unsupported; no implicit coercion')
    return {'lines': len(lines), 'integer_observations': len(lines), 'columns': 1,
            'unit': None, 'event_clock_available': False, 'request_status_available': False,
            'ordering_semantics': 'source line order preserved; event-time order unverified'}


def audit_clients(root: Path, scope: dict, catalog: dict, groups: bytes, splits: bytes, *, source_commit=None) -> dict:
    plan = client_plan(scope, catalog, groups, splits)
    # Integrity of the complete scope precedes all semantic parsing.
    content = {item['name']: _read(root, item) for item in plan}
    rows = []
    for case in CASES:
        for kind in KINDS:
            for phase, log_suffix, data_suffix in (('measurement', '.log', '_latencies.txt'),
                                                   ('declared-warm-up-filename', '.warm_up', '_latencies.warm_up')):
                prefix = f'raw_dataset/{case}/{kind}'
                summary = summarize_log(content[prefix + log_suffix])
                integers = summarize_integers(content[prefix + data_suffix])
                rows.append({'experiment': case, 'request_type': kind, 'capture': phase,
                             'log_source_path': prefix + log_suffix, 'integer_source_path': prefix + data_suffix,
                             'log': summary, 'integers': integers,
                             'integer_count_equals_reported_requests': integers['lines'] == summary['summary']['requests'],
                             'healthy_certified': False, 'slo_bucket_eligible': False})
    return {'report_version': 'gamma-client-capability/v1', 'source_commit': source_commit,
            'scope_sha256': digest(scope), 'catalog_sha256': scope['catalog_sha256'],
            'training_data_policy_hash': scope['training_data_policy_hash'],
            'group_snapshot_sha256': scope['group_snapshot_sha256'], 'split_snapshot_sha256': scope['split_snapshot_sha256'],
            'status': 'NOT_READY', 'files': len(plan), 'expanded_bytes': sum(f['size'] for f in plan),
            'experiments': len(CASES), 'capture_pairs': len(rows),
            'pairs_with_integer_request_count_mismatch': sum(not r['integer_count_equals_reported_requests'] for r in rows),
            'certified_healthy_train_windows': 0, 'certified_healthy_calibration_windows': 0,
            'verified_independent_precursor_runs': 0, 'rows': rows,
            'training_ready': False, 'activation_allowed': False, 'holdout_opened': False,
            'limitations': ['all four whole groups remain quarantined; no admission or split promotion',
                            'integer sample units, sampling and request-time alignment unverified',
                            'aggregate counts/histograms do not establish 60-second SLO windows or arrival clocks',
                            'socket and non-2xx/3xx categories do not identify an HTTP 5xx numerator',
                            'warm-up filenames and absent error lines do not certify healthy data',
                            'native request types are not mapped to Checkout/Payment/Inventory',
                            'source exporter binary/scripts/version unverified; no per-request or independence claim']}


def clean_commit(project: Path) -> str:
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=project).strip():
        raise ValueError('commit client audit implementation before measuring source capability')
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=project, text=True).strip()
