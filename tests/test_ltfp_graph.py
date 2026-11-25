from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
pytest.importorskip("pandas")

from sentinelops.ml.alibaba_training import load_config
from sentinelops.ml.ltfp_graph import build_graph_input
from sentinelops.telemetry.core import timestamp


NODES = ["checkout-service", "payment-service", "inventory-service"]
ANCHOR = datetime(2026, 11, 12, 18, 49, tzinfo=timezone.utc)


def inputs():
    config = load_config()
    observations = {}
    for n, node in enumerate(NODES):
        observations[node] = {}
        for i in range(90):
            minute = int((ANCHOR - timedelta(minutes=89-i)).timestamp()) // 60
            observations[node][minute] = {"rt": 6000 + n * 100 + i, "rate": 100 + i,
                "raw_ref": f"raw:{node}:{minute}", "available_at": timestamp(ANCHOR)}
    topo = {"identity": "topology:test:v1", "observed_at": timestamp(ANCHOR-timedelta(minutes=1)),
            "directed_edges": [(NODES[0], NODES[1]), (NODES[0], NODES[2])]}
    return config, observations, topo


def build(node_ids=NODES, observations=None, *, topology=None, self_only=False, as_of=ANCHOR):
    config, source, topo = inputs()
    return build_graph_input(node_ids, source if observations is None else observations, anchor=ANCHOR,
        as_of=as_of, config=config, topology=topo if topology is None else topology,
        source_identity={"stream": "fixture"}, self_only=self_only)


def test_a05_bidirectional_attention_preserves_directed_topology_and_self_only():
    got = build()
    assert got["attention_mask"] == [[1, 1, 1], [1, 1, 0], [1, 0, 1]]
    assert got["topology"]["directed_edges"] == sorted([[NODES[0], NODES[1]], [NODES[0], NODES[2]]])
    assert build(self_only=True)["attention_mask"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    assert "rds" not in got["node_ids"]


def test_permutation_is_id_stable_and_masked_node_does_not_hide_other_targets():
    base = build()
    order = [2, 0, 1]
    permuted = build([NODES[i] for i in order])
    for i, node in enumerate(base["node_ids"]):
        j = permuted["node_ids"].index(node)
        for base_step, perm_step in zip(base["values"][i], permuted["values"][j]):
            assert base_step == pytest.approx(perm_step, abs=1e-6)
        assert base["attention_mask"][i] == [permuted["attention_mask"][j][permuted["node_ids"].index(n)] for n in base["node_ids"]]
    broken = deepcopy(inputs()[1])
    minute = int((ANCHOR-timedelta(minutes=2)).timestamp()) // 60
    del broken[NODES[1]][minute]
    result = build(observations=broken)
    assert result["target_available"] == [True, False, True]
    assert result["attention_mask"] == [[1, 0, 1], [0, 0, 0], [1, 0, 1]]
    assert result["missing_reasons"][1] == "incomplete-feature-sequence"


@pytest.mark.parametrize("nodes,edges", [(["a", "a"], []), (NODES, [("a", "unknown")])])
def test_rejects_duplicate_ids_and_unknown_endpoints(nodes, edges):
    _, _, topo = inputs()
    topo["directed_edges"] = edges
    with pytest.raises(ValueError):
        build(nodes, topology=topo)


def test_cutoff_excludes_late_and_future_observations_and_topology():
    config, obs, topo = inputs()
    cutoff = ANCHOR+timedelta(seconds=1)
    minute = int((ANCHOR-timedelta(minutes=2)).timestamp()) // 60
    obs[NODES[0]][minute]["available_at"] = timestamp(cutoff+timedelta(microseconds=1))
    with pytest.raises(ValueError, match="topology was unavailable"):
        build_graph_input(NODES, obs, anchor=ANCHOR, as_of=cutoff, config=config,
            topology={**topo, "observed_at": timestamp(cutoff+timedelta(microseconds=1))}, source_identity={"stream":"fixture"})
    before = build(observations=obs, as_of=cutoff)
    obs[NODES[0]][minute]["available_at"] = timestamp(cutoff)
    at_cutoff = build(observations=obs, as_of=cutoff)
    assert before["target_available"][0] is False
    assert at_cutoff["target_available"][0] is True
    assert at_cutoff["graph_sha256"] == before["graph_sha256"]


def test_future_suffix_does_not_change_inputs_and_self_only_is_local():
    _, observations, _ = inputs()
    original = build(observations=observations, self_only=True)
    future = int(ANCHOR.timestamp()) // 60 + 1
    observations[NODES[0]][future] = {"rt": 999999, "rate": 999999,
        "raw_ref": "future", "available_at": timestamp(ANCHOR)}
    assert build(observations=observations, self_only=True) == original
    for row in observations[NODES[1]].values():
        row["rt"] *= 2
    changed = build(observations=observations, self_only=True)
    assert changed["values"][0] == original["values"][0]
    assert changed["values"][1] != original["values"][1]
