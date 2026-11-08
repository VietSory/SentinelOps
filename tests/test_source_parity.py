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
