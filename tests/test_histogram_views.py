import itertools
import math

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from services.common.observability import duration_histogram_views
from sentinelops.telemetry.core import histogram_quantile


def test_sdk_histogram_resolves_1200ms_fault_without_default_2500ms_bucket_bias():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader], views=duration_histogram_views())
    histogram = provider.get_meter("test").create_histogram("sentinelops_http_request_duration_ms", unit="ms")
    for _ in range(100):
        histogram.record(1200)
    data = reader.get_metrics_data()
    point = data.resource_metrics[0].scope_metrics[0].metrics[0].data.data_points[0]
    buckets = tuple(zip((*point.explicit_bounds, math.inf), itertools.accumulate(point.bucket_counts)))
    assert 1200 <= histogram_quantile(buckets) <= 1250
    provider.shutdown()
