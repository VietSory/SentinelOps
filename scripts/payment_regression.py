"""Immutable Payment latency-regression image definition.

The EKS image is deliberately based on a digest in the application repository.
The only local exception is the owned kind testbed's ``sentinelops-business``
repository, passed explicitly by :mod:`scripts.run_s2_h4`.
"""
from __future__ import annotations

import json
import re


APPLICATION_REPOSITORY = 'sentinelops/application'
LOCAL_TESTBED_REPOSITORY = 'sentinelops-business'
_DIGEST_REF = re.compile(
    r'^(?P<registry>[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?)/'
    r'(?P<repository>[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*)'
    r'@sha256:(?P<digest>[0-9a-f]{64})$'
)

# These anchors intentionally fail the image build when Payment's request path
# changes. The fault is baked into this image; it has no environment toggle.
PATCH = r"""from pathlib import Path
p = Path('/app/services/payment/app.py')
s = p.read_text()
gate = 'fault = FaultState()'
marker = '    await fault.apply(SERVICE_NAME)'
assert s.count(marker) == s.count(gate) == 1
s = s.replace(gate, 'import asyncio\nimport time\n'+gate+'\n_s2_regression_after = time.monotonic() + 130')
s = s.replace(marker, '    if time.monotonic() >= _s2_regression_after:\n        await asyncio.sleep(0.95)\n'+marker, 1)
p.write_text(s)
"""


def validate_base(base: str, *, repository: str = APPLICATION_REPOSITORY) -> str:
    """Return a valid digest-pinned base in *repository*, or raise ValueError."""
    match = _DIGEST_REF.fullmatch(base)
    if not match or match['repository'] != repository:
        raise ValueError(
            f'BASE must be a digest-pinned <registry>/{repository}@sha256:<64hex> reference'
        )
    return base


def dockerfile(base: str, *, repository: str = APPLICATION_REPOSITORY) -> str:
    """Validate *base* and return the canonical immutable regression Dockerfile."""
    validate_base(base, repository=repository)
    return (
        'ARG BASE\n'
        'FROM ${BASE}\n'
        'ARG BASE\n'
        'LABEL sentinelops.dev/variant="payment-regression" \\\n'
        '      sentinelops.dev/base-digest="${BASE}"\n'
        'RUN ' + json.dumps(['python', '-c', PATCH]) + '\n'
    )
