from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tarfile

import pytest

pytest.importorskip("pandas")
from scripts.audit_ltfp_graph_support import read_graph, support
from sentinelops.ml.alibaba_training import split_of


@pytest.mark.parametrize("minutes", [(0, 1, 2), (0, 2)])
def test_trace_presence_is_independent_of_train_edge_eligibility(tmp_path, minutes):
    body = "timestamp,um,dm,rpctype\n" + "".join(
        f"{minute * 60000},MS_1,MS_1,rpc\n" for minute in minutes
    )
    path = tmp_path / "CallGraph_0.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("CallGraph_0.csv")
        member.size = len(body)
        archive.addfile(member, BytesIO(body.encode()))
    digest = sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(digest)
    if len(minutes) < 3:
        with pytest.raises(ValueError, match="missing trace minutes"):
            read_graph([path])
        return
    graph, receipts = read_graph([path])
    assert graph == {0: set(), 1: set(), 2: set()}
    assert receipts == {path.name: digest}


def test_support_counts_nodes_once_and_checks_both_endpoints_at_the_same_minute():
    result = support({("a", "b"), ("b", "a"), ("a", "c")}, {4: {"a", "b", "c"}, 5: {"a"}}, 4, 5)
    assert result == {"period": [4, 5], "metric_node_minutes": 4, "on_edge_node_minutes": 3,
                      "without_observed_neighbor": 1, "usable_directed_edge_minutes": 3}


def test_calibration_graph_keeps_direction_and_excludes_cross_split_edges(tmp_path):
    groups = {'train': [], 'calibration': []}
    for index in range(100):
        name = f'MS_{index}'
        role = split_of(name)
        if role in groups and len(groups[role]) < 2:
            groups[role].append(name)
    train, _ = groups['train']
    left, right = groups['calibration']
    body = (f'timestamp,um,dm,rpctype\n0,{left},{right},rpc\n'
            f'60000,{left},{train},rpc\n120000,{right},{right},rpc\n').encode()
    path = tmp_path / 'CallGraph_0.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        member = tarfile.TarInfo('CallGraph_0.csv')
        member.size = len(body)
        archive.addfile(member, BytesIO(body))
    Path(str(path) + '.sha256').write_text(sha256(path.read_bytes()).hexdigest())
    graph, _ = read_graph([path], role='calibration')
    assert graph == {0: {(left, right)}, 1: set(), 2: set()}
    with pytest.raises(ValueError, match='only train or calibration'):
        read_graph([path], role='evaluation')
