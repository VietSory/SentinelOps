import pytest

pytest.importorskip("pandas")

from datetime import timedelta

from sentinelops.contracts import utc_time

from sentinelops.ml.alibaba_live import project_graph_window
from sentinelops.ml.alibaba_training import load_config
from sentinelops.ml.ltfp_graph import build_graph_input
from sentinelops.telemetry.prometheus import decode_capture, mean_latency_ms
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.prometheus import SOURCE_PROFILE_V2
from sentinelops.telemetry.store import ArtifactStore
from test_health_source import capture
from test_ltfp_graph import ANCHOR


def test_offline_and_accepted_projectors_share_vectors_masks_refs_and_cutoff(tmp_path):
    config = load_config()
    store = ArtifactStore(tmp_path)
    identity = {"source_profile": SOURCE_PROFILE_V2, "environment": "fixture",
                "worker_version": "continuous-health/v1"}
    previous, history = None, []
    start = ANCHOR-timedelta(minutes=89)
    for i in range(90):
        end = start + timedelta(minutes=i)
        raws = [capture(service, end, profile=SOURCE_PROFILE_V2) for service in BUSINESS_ROUTES]
        for raw in raws:
            raw["collected_at"] = timestamp(end)
        history.append(raws)
        refs = [store.write("raw", raw) for raw in raws]
        previous = store.write("manifests", {"identity": identity, "stream_id": stable_id(identity),
            "bucket_end": timestamp(end), "evaluated_at": timestamp(end),
            "current_raw_refs": refs, "previous_batch_ref": previous})
    topology = {"identity": "topology:fixture:v1", "observed_at": timestamp(ANCHOR-timedelta(minutes=1)),
                "directed_edges": [("checkout-service", "payment-service"),
                                   ("checkout-service", "inventory-service")]}
    result = project_graph_window(store, previous, node_ids=list(BUSINESS_ROUTES), topology=topology,
                                  as_of=ANCHOR, config=config)
    observations = {service: {} for service in BUSINESS_ROUTES}
    for raws in history:
        for raw in raws:
            observation = decode_capture(raw, environment="fixture")
            minute = int(utc_time(raw["bucket_end"]).timestamp()) // 60
            observations[raw["service"]][minute] = {"rt": mean_latency_ms(raw),
                "rate": observation["measurements"]["request_count"], "raw_ref": store.write("raw", raw),
                "available_at": raw["collected_at"]}
    offline = build_graph_input(list(BUSINESS_ROUTES), observations, anchor=ANCHOR, as_of=ANCHOR,
        config=config, topology=topology, source_identity=identity)
    assert offline == result
    assert result["node_ids"] == list(BUSINESS_ROUTES)
    assert result["step_minutes"] == [int((ANCHOR-timedelta(minutes=i)).timestamp())//60 for i in range(4,-1,-1)]
    assert result["units"] == {"rt": "milliseconds", "rate": "request_count"}
    assert all(result["target_available"])
    assert all(len(refs) > 0 for node in result["raw_refs"] for refs in node)
    late = store.read(previous)
    raw = store.read(late["current_raw_refs"][1])
    raw["collected_at"] = timestamp(ANCHOR+timedelta(seconds=1))
    late["current_raw_refs"][1] = store.write("raw", raw)
    late["evaluated_at"] = timestamp(ANCHOR)
    late_ref = store.write("manifests", late)
    after = project_graph_window(store, late_ref, node_ids=list(BUSINESS_ROUTES), topology=topology,
                                 as_of=ANCHOR, config=config)
    assert after["target_available"][1] is False
    assert after["attention_mask"][0][1] == 0


def test_checker_compares_independent_observations_and_rejects_numeric_drift(tmp_path, monkeypatch):
    from collections import defaultdict
    import scripts.check_ltfp_graph_parity as checker
    from copy import deepcopy
    import pytest

    store = checker.Replay(tmp_path)
    rows = defaultdict(list)
    for i in range(90):
        end = ANCHOR-timedelta(minutes=89-i)
        for service in BUSINESS_ROUTES:
            raw = capture(service, end, profile=SOURCE_PROFILE_V2)
            ref = 'fixture:' + stable_id(raw)
            store.objects[ref] = raw
            rows[timestamp(end)].append((ref, raw))
    identity = {'source_profile': SOURCE_PROFILE_V2, 'environment': 'fixture',
                'worker_version': 'continuous-health/v1'}
    topology = {'identity': 'fixture', 'observed_at': timestamp(ANCHOR-timedelta(minutes=90)),
                'directed_edges': [('checkout-service', 'payment-service')]}
    options = dict(anchor=ANCHOR, cutoff=ANCHOR+timedelta(seconds=30), identity=identity,
                   topology=topology, config=load_config())
    result = checker.compare(store, rows, **options)
    assert all(result['target_available'])
    original = checker.project_graph_window

    def altered(*args, **kwargs):
        value = deepcopy(original(*args, **kwargs))
        value['values'][0][0][0] += 0.1
        return value

    monkeypatch.setattr(checker, 'project_graph_window', altered)
    with pytest.raises(AssertionError, match='graph inputs differ'):
        checker.compare(store, rows, **options)


def test_replay_rejects_altered_archive(tmp_path):
    from scripts.check_ltfp_graph_parity import Replay
    import json
    import pytest

    (tmp_path/'wrong.json').write_text(json.dumps({'changed': True}))
    with pytest.raises(ValueError, match='checksum'):
        Replay(tmp_path).read('artifact://wrong.json')
