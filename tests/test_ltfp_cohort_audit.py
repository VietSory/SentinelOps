"""Time boundaries for exploratory Alibaba graph cohort support."""
from collections import defaultdict
import hashlib
import io
import json
import tarfile

import pytest

pd = pytest.importorskip('pandas')

from scripts import audit_ltfp_cohort
from scripts.audit_ltfp_cohort import runtime_feature_minutes, validate_partitions
from sentinelops.ml.alibaba_training import load_config, service_window_arrays, split_of
from sentinelops.telemetry.core import stable_id


def test_inclusive_dependency_bounds_and_disjoint_partitions():
    partitions = {'train': (0, 239), 'calibration': (240, 359)}
    validate_partitions(partitions, {'train': (68, 232), 'calibration': (308, 352)})
    for windows in (
        {'train': (67, 232), 'calibration': (308, 352)},
        {'train': (68, 233), 'calibration': (308, 352)},
        {'train': (68, 232), 'calibration': (307, 352)},
        {'train': (68, 232), 'calibration': (308, 353)},
    ):
        with pytest.raises(ValueError, match='dependency partition'):
            validate_partitions(partitions, windows)
    with pytest.raises(ValueError, match='overlap'):
        validate_partitions({'train': (0, 240), 'calibration': (240, 359)},
                            {'train': (68, 232), 'calibration': (308, 352)})


def test_service_eligibility_excludes_minutes_outside_its_partition(tmp_path, monkeypatch):
    services = {'train': [], 'calibration': []}
    for index in range(100):
        name = f'MS_{index}'
        role = split_of(name)
        if role in services and len(services[role]) < 2:
            services[role].append(name)
    train, insufficient = services['train']
    calibration = services['calibration'][0]
    data = pd.DataFrame([
        {'msname': name, 'minute': minute, 'http_rt_ms': 10.0, 'http_mcr_sum': 1.0}
        for name in (train, insufficient, calibration) for minute in range(360)
        if not (name == insufficient and 119 <= minute < 180)
    ])
    provenance = [{'archive': str(index), 'archive_sha256': 'a', 'parquet_sha256': 'b'}
                  for index in range(120)]
    report = tmp_path / 'data/fit-20261113/report.json'
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({'source_archives': provenance, 'config_sha256': stable_id(load_config())}))
    monkeypatch.setattr(audit_ltfp_cohort, 'load_minutes', lambda _: (data.copy(), provenance))
    monkeypatch.setattr(audit_ltfp_cohort, 'file_sha256', lambda _: audit_ltfp_cohort.FIT_REPORT_SHA)
    monkeypatch.setattr(audit_ltfp_cohort, 'read_graph', lambda *_: (
        {'train': defaultdict(set), 'calibration': defaultdict(set)}, {}))
    result = audit_ltfp_cohort.audit(tmp_path, [],
        {'train': (0, 179), 'calibration': (180, 359)},
        {'train': (68, 172), 'calibration': (248, 352)})
    train_result = next(item for item in result['results'] if item['role'] == 'train')
    assert train_result['excluded_services_insufficient_partition_minutes'] == 1
    assert train_result['services'] == 1
    assert train_result['negative'] == 54
    assert train_result['negative_services'] == 1
    report.write_text(json.dumps({'source_archives': provenance, 'config_sha256': 'wrong'}))
    with pytest.raises(ValueError, match='configuration differs'):
        audit_ltfp_cohort.audit(tmp_path, [],
            {'train': (0, 179), 'calibration': (180, 359)},
            {'train': (68, 172), 'calibration': (248, 352)})


def test_graph_rejects_cross_split_edges_and_future_topology(tmp_path):
    roles = {'train': [], 'calibration': []}
    for index in range(100):
        name = f'MS_{index}'
        role = split_of(name)
        if role in roles and len(roles[role]) < 2:
            roles[role].append(name)
    train_a, train_b = roles['train']
    calibration_a, calibration_b = roles['calibration']
    body = (f'timestamp,um,dm,rpctype\n0,{train_a},{train_b},rpc\n'
            f'60000,{calibration_a},{calibration_b},http\n'
            f'120000,{train_a},{calibration_a},rpc\n').encode()
    path = tmp_path / 'CallGraph_0.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        member = tarfile.TarInfo('CallGraph_0.csv')
        member.size = len(body)
        archive.addfile(member, io.BytesIO(body))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + '.sha256').write_text(f'{digest}  {path.name}\n')
    graph, receipts = audit_ltfp_cohort.read_graph([path], first_step=3)
    assert graph['train'][train_a] == {train_b}
    assert graph['calibration'][calibration_a] == {calibration_b}
    assert receipts == {path.name: digest}
    with pytest.raises(ValueError, match='unavailable before feature sequence'):
        audit_ltfp_cohort.read_graph([path], first_step=2)


def test_runtime_feature_steps_require_an_observation_at_the_anchor():
    series = pd.DataFrame({'rt': [10.0] * 180, 'rate': [1.0] * 180}, index=range(180))
    series = series.drop(index=161)
    config = load_config()
    assert 161 in service_window_arrays(series, config)['minute']
    assert 161 not in runtime_feature_minutes(series, config)
    series.loc[161] = (10.0, 0.0)
    assert 161 not in runtime_feature_minutes(series.sort_index(), config)
