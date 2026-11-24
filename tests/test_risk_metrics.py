from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from sentinelops.workers.risk import RiskMetrics


def test_last_scored_timestamp_requires_a_valid_risk_score():
    reader = InMemoryMetricReader()
    metrics = RiskMetrics(reader, thresholds={})
    episode = {'active': False}

    def series():
        return {metric.name: metric.data.data_points
                for resource in reader.get_metrics_data().resource_metrics
                for scope in resource.scope_metrics for metric in scope.metrics}

    try:
        metrics.publish('payment-service', {'state': 'unavailable'}, episode, scored_at=100)
        assert 'sentinelops_risk_last_scored_unixtime' not in series()
        metrics.publish('payment-service', {'state': 'normal', 'risk_score': 0}, episode, scored_at=160)
        assert series()['sentinelops_risk_last_scored_unixtime'][0].value == 160
        metrics.publish('payment-service', {'state': 'unavailable'}, episode, scored_at=220)
        assert 'sentinelops_risk_last_scored_unixtime' not in series()
    finally:
        metrics.provider.shutdown()
