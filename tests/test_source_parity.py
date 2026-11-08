"""Predeclared parity criteria helpers; no Kubernetes or model is used here."""
from scripts.check_model_source_parity import COUNT_TOLERANCE, bucket_of, capture_shape

BOUNDS = ["0.0", "5.0", "10.0", "25.0", "50.0", "+Inf"]


def raw(bounds=BOUNDS, query="sum(increase(x[60s]))"):
    return {"source_profile": "prometheus-increase-60s-5s/v1",
            "queries": {"requests": {"expression": query},
                        "buckets": {"expression": "b", "response": {"data": {"result": [{"metric": {"le": b}} for b in bounds]}}}}}


def test_same_bucket_p95_uses_the_instrumented_histogram_edges():
    assert bucket_of(4.25, BOUNDS) == bucket_of(4.75, BOUNDS) == 5.0
    assert bucket_of(5.0, BOUNDS) == 5.0 and bucket_of(5.01, BOUNDS) == 10.0
    assert bucket_of(24.3, BOUNDS) != bucket_of(25.2, BOUNDS)
    assert bucket_of(80.0, BOUNDS) == float("inf")


def test_source_shape_compares_profile_queries_and_ordered_bounds():
    assert capture_shape(raw()) == capture_shape(raw(bounds=list(reversed(BOUNDS))))
    assert capture_shape(raw()) != capture_shape(raw(query="other"))
    assert capture_shape(raw()) != capture_shape(raw(bounds=BOUNDS[:-2] + ["+Inf"]))
    assert COUNT_TOLERANCE == .05


def test_source_shape_manifest_is_pinned_and_bound_to_the_candidate_dataset(tmp_path):
    import hashlib, json
    import pytest
    from scripts.check_model_source_parity import load_source_shapes
    routes = {s: {"profile": "p", "queries": {}, "latency_bounds_ms": BOUNDS}
              for s in ("checkout-service", "payment-service", "inventory-service")}
    value = {"shapes_version": "training-source-shapes/v1", "dataset_ref": "artifact://manifests/" + "d" * 64 + ".json",
             "routes": routes}
    path = tmp_path / "shapes.json"
    path.write_bytes(json.dumps(value).encode())
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert load_source_shapes(path, sha, {"dataset_ref": value["dataset_ref"]})[1] == routes
    for index, digest in (({"dataset_ref": "artifact://manifests/" + "0" * 64 + ".json"}, sha),
                          ({"dataset_ref": value["dataset_ref"]}, "0" * 64)):
        with pytest.raises(ValueError):
            load_source_shapes(path, digest, index)


def test_c5_v2_accepts_same_or_adjacent_bucket_only():
    from scripts.check_model_source_parity import CRITERIA_VERSION, bucket_index
    assert CRITERIA_VERSION == 'parity-criteria/v2'
    # The observed v1 failure: interpolated 26.67 ms vs exact 24.93 ms across the 25 ms edge.
    assert abs(bucket_index(26.6667, BOUNDS) - bucket_index(24.93, BOUNDS)) == 1
    assert abs(bucket_index(4.25, BOUNDS) - bucket_index(4.75, BOUNDS)) == 0
    assert abs(bucket_index(4.0, BOUNDS) - bucket_index(30.0, BOUNDS)) == 3
