"""Build/install a wheel outside the checkout and exercise its runtime contracts."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CHECK = r'''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sentinelops
assert Path(sentinelops.__file__).resolve().is_relative_to(Path(sys.argv[1]))
from sentinelops.main import app
from sentinelops.contracts import load_schema
from fastapi.testclient import TestClient
for name in ('telemetry', 'feature-window', 'detection-result', 'degradation-risk',
             'incident-evidence-record', 'explanation', 'remediation-action',
             'observation-envelope', 'feature-envelope'):
    assert load_schema(name + '.schema.json')['type'] == 'object'
client = TestClient(app)
assert client.get('/health').status_code == 200
payload = {'schema_version':'1.0', 'service':'payment-service',
    'window_start':'2026-10-25T00:00:00Z', 'window_end':'2026-10-25T00:05:00Z',
    'features':{'latency': 100}}
assert client.post('/v1/detect', json=payload).status_code == 200
assert client.post('/v1/detect', json={}).status_code == 422
assert client.post('/v1/incidents/demo').status_code == 200
print('INSTALLED WHEEL SMOKE TEST: PASS')
'''


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sentinelops-wheel-") as temp:
        workspace = Path(temp)
        subprocess.run([
            sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
            "--wheel-dir", str(workspace), str(ROOT),
        ], check=True, cwd=workspace)
        wheel, = workspace.glob("sentinelops-*.whl")
        installed = workspace / "site"
        subprocess.run([
            sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(installed), str(wheel),
        ], check=True, cwd=workspace)
        subprocess.run([
            sys.executable, "-I", "-c", CHECK, str(installed),
        ], check=True, cwd=workspace)


if __name__ == "__main__":
    main()
