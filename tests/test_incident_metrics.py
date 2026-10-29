import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from sentinelops.telemetry.store import ArtifactStore
from sentinelops.workers.metrics import IncidentWorkerMetrics


def test_consumer_metrics_have_separate_resource_bounded_slots_and_no_fake_initial_source(tmp_path):
    reader = InMemoryMetricReader()
    metrics = IncidentWorkerMetrics(reader, environment="test", instance="incidents-0", clock=lambda: 200.)
    try:
        metrics.heartbeat(ready=False)
        data = reader.get_metrics_data()
        assert data.resource_metrics[0].resource.attributes["service.name"] == "sentinelops-incident-worker"
        names = {m.name for resource in data.resource_metrics for scope in resource.scope_metrics for m in scope.metrics}
        assert "sentinelops_incident_groups" in names and "sentinelops_rca_abstentions" in names
        assert "sentinelops_worker_source_available" not in names
        metrics.observe_result({"status": "BUSY"}, ArtifactStore(tmp_path))
        with pytest.raises(ValueError):
            metrics.error("incident-id-high-cardinality")
    finally:
        metrics.close()
    with pytest.raises(ValueError):
        IncidentWorkerMetrics(InMemoryMetricReader(), environment="test", instance="health-0")
