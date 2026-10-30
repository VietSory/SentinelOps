from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from io import BytesIO

import httpx
import pytest

from sentinelops.public_data.gamma import canonical_bytes
from sentinelops.public_data.gamma_archive import archive_catalog
import sentinelops.public_data.gamma_clients as clients
from test_gamma_archive import range_transport, zip_blob


LOG = (b'Running 1m test @ http://private.invalid/secret\n'
       b'Latency Distribution (HdrHistogram - Recorded Latency)\n'
       b'#[Max = 123.0, Total count = 9]\n'
       b'10 requests in 1.50m, 2.00MB read\n')


@pytest.fixture
def capture(tmp_path):
    files = {f'raw_dataset/{case}/{kind}{suffix}': (LOG if suffix in ('.log', '.warm_up') else b'123\n456\n')
             for case in clients.CASES for kind in clients.KINDS for suffix in clients.SUFFIXES}
    blob = zip_blob(files)
    transport, _ = range_transport(blob)
    with httpx.Client(transport=transport) as client:
        catalog, reader, archive = archive_catalog(client)
        archive.close()
        reader.close()
    groups = canonical_bytes({'groups': [{'group_id': str(i), 'experiments': [case], 'admitted_cases': 0,
                                         'final_holdout_eligible': False} for i, case in enumerate(clients.CASES)]})
    splits = canonical_bytes({'assignments': [{'group_id': str(i), 'experiments': [case], 'split': 'quarantine'}
                                             for i, case in enumerate(clients.CASES)]})
    scope = {'scope_version': 'gamma-client-captures/v1', 'catalog_sha256': clients.digest(catalog),
             'training_data_policy_hash': catalog['training_data_policy_hash'],
             'group_snapshot_sha256': hashlib.sha256(groups).hexdigest(),
             'split_snapshot_sha256': hashlib.sha256(splits).hexdigest(), 'experiments': list(clients.CASES),
             'files': [{**f, 'sha256': hashlib.sha256(files[f['name']]).hexdigest()} for f in catalog['files']],
             'training_ready': False, 'activation_allowed': False}
    root = tmp_path / 'raw'
    for name, data in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root, scope, catalog, groups, splits, blob


def test_whole_scope_audit_preserves_unknowns_and_never_prints_raw_urls(capture):
    root, scope, catalog, groups, splits, _ = capture
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    first = clients.audit_clients(root, scope, catalog, groups, splits, source_commit='a' * 40)
    assert first == clients.audit_clients(root, scope, catalog, groups, splits, source_commit='a' * 40)
    assert first['files'] == 48 and first['capture_pairs'] == first['pairs_with_integer_request_count_mismatch'] == 24
    assert first['status'] == 'NOT_READY' and first['certified_healthy_train_windows'] == 0
    assert first['training_ready'] is False and first['activation_allowed'] is False and first['holdout_opened'] is False
    for pair in first['rows']:
        assert pair['log']['summary']['duration_seconds'] == '90.00'
        assert pair['log']['summary']['requests'] == 10 and pair['log']['recorded_histogram_count'] == 9
        assert pair['log']['socket_errors'] is None and pair['log']['non_2xx_3xx_responses'] is None
        assert pair['log']['http_5xx_count'] is None and pair['integers']['unit'] is None
        assert pair['healthy_certified'] is False and pair['slo_bucket_eligible'] is False
    assert 'private.invalid' not in json.dumps(first) and 'secret' not in json.dumps(first)
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize('mutation', ['holdout', 'calibration', 'train', 'admitted', 'final-eligible', 'duplicate-role'])
def test_role_preflight_fails_before_any_raw_read_or_network_even_with_rehashed_snapshots(capture, monkeypatch, mutation):
    root, scope, catalog, gb, sb, _ = capture
    groups, splits = json.loads(gb), json.loads(sb)
    if mutation in ('holdout', 'calibration', 'train'):
        splits['assignments'][-1]['split'] = mutation
    elif mutation == 'admitted':
        groups['groups'][-1]['admitted_cases'] = 1
    elif mutation == 'final-eligible':
        groups['groups'][-1]['final_holdout_eligible'] = True
    else:
        splits['assignments'].append(deepcopy(splits['assignments'][-1]))
    gb, sb = canonical_bytes(groups), canonical_bytes(splits)
    scope['group_snapshot_sha256'], scope['split_snapshot_sha256'] = hashlib.sha256(gb).hexdigest(), hashlib.sha256(sb).hexdigest()
    monkeypatch.setattr(clients, '_read', lambda *a: pytest.fail('raw bytes opened before role admission'))
    with pytest.raises(ValueError, match='quarantined'):
        clients.audit_clients(root, scope, catalog, gb, sb)
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('unadmitted scope reached network'))) as client:
        with pytest.raises(ValueError, match='quarantined'):
            clients.download_clients(client, root, scope, catalog, gb, sb)


def test_self_declared_group_hash_cannot_override_actual_snapshot(capture):
    root, scope, catalog, gb, sb, _ = capture
    forged = json.loads(gb)
    forged['_file_sha256'] = scope['group_snapshot_sha256']
    with pytest.raises(ValueError, match='snapshot identity'):
        clients.audit_clients(root, scope, catalog, forged, sb)


@pytest.mark.parametrize('mutation', ['later-hidden-member', 'missing-file', 'duplicate-file', 'extra-trace', 'changed-size'])
def test_entire_inventory_is_admitted_before_read(capture, monkeypatch, mutation):
    root, scope, catalog, gb, sb, _ = capture
    if mutation == 'later-hidden-member':
        scope['files'][-1]['name'] = 'raw_dataset/heldout/compose.log'
    elif mutation == 'missing-file':
        scope['files'].pop()
    elif mutation == 'duplicate-file':
        scope['files'][-1] = deepcopy(scope['files'][0])
    elif mutation == 'extra-trace':
        scope['files'].append({'name': 'raw_dataset/other/full_traces.json'})
    else:
        scope['files'][-1]['size'] += 1
    monkeypatch.setattr(clients, '_read', lambda *a: pytest.fail('raw bytes opened before full inventory admission'))
    with pytest.raises(ValueError):
        clients.audit_clients(root, scope, catalog, gb, sb)


def test_corrupt_last_member_is_not_parsed_or_repaired(capture, monkeypatch):
    root, scope, catalog, gb, sb, _ = capture
    last = root / sorted(scope['files'], key=lambda f: f['name'])[-1]['name']
    last.write_bytes(b'corrupt source')
    monkeypatch.setattr(clients, 'summarize_log', lambda *a: pytest.fail('semantic parsing before complete integrity'))
    with pytest.raises(ValueError, match='frozen size'):
        clients.audit_clients(root, scope, catalog, gb, sb)
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('corrupt cache reached network'))) as client:
        with pytest.raises(ValueError, match='frozen size'):
            clients.download_clients(client, root, scope, catalog, gb, sb)
    assert last.read_bytes() == b'corrupt source'


def test_symlink_cache_is_denied(capture, tmp_path):
    root, scope, catalog, gb, sb, _ = capture
    p = root / scope['files'][0]['name']
    real = tmp_path / 'outside'
    p.rename(real)
    p.symlink_to(real)
    with pytest.raises(ValueError, match='symlinks'):
        clients.audit_clients(root, scope, catalog, gb, sb)


def test_fresh_bounded_archive_download_and_offline_verified_cache(capture, tmp_path):
    _, scope, catalog, gb, sb, blob = capture
    root = tmp_path / 'fresh'
    transport, requests = range_transport(blob)
    with httpx.Client(transport=transport) as client:
        first = clients.download_clients(client, root, scope, catalog, gb, sb)
    assert first['files'] == 48 and first['cached_files'] == 0 and first['range_requests'] == 1
    assert len(requests) == 1
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('fully verified cache reached network'))) as client:
        second = clients.download_clients(client, root, scope, catalog, gb, sb)
    assert second['cached_files'] == 48 and second['range_requests'] == 0
    assert clients.audit_clients(root, scope, catalog, gb, sb)['capture_pairs'] == 24


def test_corrupt_archive_payload_cannot_be_published(capture, tmp_path):
    _, scope, catalog, gb, sb, blob = capture
    first = min(catalog['files'], key=lambda f: f['header_offset'])
    with ZipFile(BytesIO(blob)) as z:
        info = z.getinfo(first['name'])
        offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    corrupted = blob[:offset] + bytes([blob[offset] ^ 1]) + blob[offset + 1:]
    transport, _ = range_transport(corrupted)
    root = tmp_path / 'corrupt-download'
    with httpx.Client(transport=transport) as client:
        with pytest.raises((BadZipFile, ValueError)):
            clients.download_clients(client, root, scope, catalog, gb, sb)
    assert not list(root.rglob('*'))


def test_aggregate_error_categories_are_retained_without_fabricating_http5xx():
    result = clients.summarize_log(LOG + b'Socket errors: connect 1, read 2, write 3, timeout 4\nNon-2xx or 3xx responses: 7\n')
    assert result['socket_errors'] == {'connect': 1, 'read': 2, 'write': 3, 'timeout': 4}
    assert result['non_2xx_3xx_responses'] == 7 and result['http_5xx_count'] is None
    assert result['event_clock_available'] is False and result['per_bucket_request_denominator_available'] is False


@pytest.mark.parametrize('blob', [b'', LOG + LOG, b'10 requests but no duration\n'])
def test_ambiguous_or_missing_summary_is_explicit_failure(blob):
    with pytest.raises(ValueError, match='summary'):
        clients.summarize_log(blob)


@pytest.mark.parametrize('blob', [b'', b'12\n-1\n', b'12,34\n', b'12 ms\n', b'18446744073709551616\n'])
def test_integer_format_never_guesses_units_or_coerces_other_columns(blob):
    with pytest.raises(ValueError, match='integer stream'):
        clients.summarize_integers(blob)


def test_native_unsigned_integer_values_have_no_invented_clock_or_unit():
    result = clients.summarize_integers(b'18446744073709551615\n1700000000\n')
    assert result['integer_observations'] == 2 and result['unit'] is None and result['event_clock_available'] is False


def test_decode_and_line_budgets_precede_large_allocations():
    with pytest.raises(ValueError, match='byte budget'):
        clients.summarize_log(b'x' * (clients.MAX_FILE + 1))
    with pytest.raises(ValueError, match='line budget'):
        clients.summarize_integers(b'1\n' * (clients.MAX_LINES + 1))
    with pytest.raises(ValueError, match='line budget'):
        clients.summarize_integers(b'1\r' * (clients.MAX_LINES + 1))


def test_context_loader_checks_actual_snapshot_and_catalog_bytes(capture, tmp_path):
    _, scope, catalog, gb, sb, _ = capture
    paths = [tmp_path / name for name in ('scope.json', 'catalog.json', 'groups.json', 'splits.json')]
    for p, data in zip(paths, (canonical_bytes(scope), canonical_bytes(catalog), gb, sb)):
        p.write_bytes(data)
    assert clients.load_context(*paths) == (scope, catalog, gb, sb)
    paths[-1].write_bytes(sb + b' ')
    with pytest.raises(ValueError, match='snapshot identity'):
        clients.load_context(*paths)
