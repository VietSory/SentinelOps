import asyncio
import json

from scripts.incident_worker_evidence import IncidentWorkerEvidence
from sentinelops.telemetry.store import ArtifactStore


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
