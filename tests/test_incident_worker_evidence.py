import asyncio
import json
from copy import deepcopy

import pytest

from scripts.incident_worker_evidence import IncidentWorkerEvidence, verify_scenario_group
from sentinelops.telemetry.store import ArtifactStore


def test_s5_shared_checkout_requires_ambiguity_even_if_result_is_mislabeled():
    group = {"affected_services": ["checkout-service"], "grouping_reason": "ambiguous-shared-consumer",
             "structural_candidates": ["inventory-service", "payment-service"]}
    result = {"abstention_reasons": ["ambiguous-correlation"]}
    verify_scenario_group("S5", group, result)
    for key, value in (("grouping_reason", "single-service"), ("structural_candidates", ["payment-service"])):
        changed = deepcopy(group)
        changed[key] = value
        with pytest.raises(ValueError, match="Checkout"):
            verify_scenario_group("S5", changed, result)
    with pytest.raises(ValueError, match="ambiguity"):
        verify_scenario_group("S5", group, {"abstention_reasons": []})


@pytest.mark.parametrize("service", ["inventory-service", "payment-service"])
def test_s5_independent_roots_cannot_be_replaced_by_shared_candidates(service):
    group = {"affected_services": [service], "grouping_reason": "single-service", "structural_candidates": [service]}
    verify_scenario_group("S5", group, {"abstention_reasons": []})
    group["structural_candidates"] = ["inventory-service", "payment-service"]
    with pytest.raises(ValueError, match="independent root"):
        verify_scenario_group("S5", group, {"abstention_reasons": []})


def test_restart_uses_same_original_roots_and_no_experiment_controls_in_consumer_arguments(tmp_path, monkeypatch):
    created = []
    class Process:
        def __init__(self, argv, **kwargs):
            self.argv, self.pid, self.returncode = argv, 1000 + len(created), None
            created.append(self)
            kwargs['stdout'].write(json.dumps({'status': 'READY', 'consumer_id': 'a'*64})+'\n')
            kwargs['stdout'].flush()
        def poll(self): return self.returncode
        def kill(self): self.returncode = -9
        def terminate(self): self.returncode = 0
        def wait(self, timeout): return self.returncode
    monkeypatch.setattr('scripts.incident_worker_evidence.subprocess.Popen', Process)
    class Connection:
        async def fetchval(self, *args): return 1
    class Acquire:
        async def __aenter__(self): return Connection()
        async def __aexit__(self, *args): pass
    class Pool:
        def acquire(self): return Acquire()
    source = ArtifactStore(tmp_path/'original-health')
    observer = IncidentWorkerEvidence(tmp_path/'observer', source, 'b'*64, 'owned-fixture')
    observer.spawn()
    async def run():
        await observer.observe(Pool())
        assert observer.crashed and len(created) == 3 and created[0].returncode == -9
        await observer.stop()
    asyncio.run(run())
    for process in created:
        argv = process.argv
        assert argv[argv.index('--source-store')+1] == str(source.root)
        assert argv[argv.index('--store')+1] == str(observer.store.root)
        assert argv[argv.index('--health-stream')+1] == 'b'*64
        assert not any('truth' in arg or 'scenario' in arg or 'fault' in arg for arg in argv)
    assert all(process.returncode is not None for process in created)
