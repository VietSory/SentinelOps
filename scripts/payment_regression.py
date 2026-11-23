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


# ---- Gradual regression (DEMO_UPGRADE_SPEC WP5): latency creeps up after a deploy ----

GRADUAL_DEFAULTS = {'start_delay_s': 60, 'ramp_s': 600, 'max_ms': 1200}
GRADUAL_BOUNDS = {'start_delay_s': (0, 120), 'ramp_s': (300, 900), 'max_ms': (800, 2000)}


def gradual_delay_ms(elapsed_s, *, start_delay_s, ramp_s, max_ms):
    """Added Payment latency: 0 until start_delay_s, then linear to max_ms over ramp_s, then flat."""
    if elapsed_s < start_delay_s:
        return 0
    return int(min(max_ms, max_ms * (elapsed_s - start_delay_s) / ramp_s))


def _gradual_params(params):
    values = {**GRADUAL_DEFAULTS, **params}
    if set(values) != set(GRADUAL_DEFAULTS):
        raise ValueError('unknown gradual regression parameter')
    for key, (low, high) in GRADUAL_BOUNDS.items():
        if type(values[key]) is not int or not low <= values[key] <= high:
            raise ValueError(f'{key} must be an integer in [{low}, {high}]')
    return values


def gradual_patch(**params):
    """Build-time patch: the delay curve is baked into the image, no environment toggle."""
    values = _gradual_params(params)
    import inspect
    source = inspect.getsource(gradual_delay_ms)
    return (
        "from pathlib import Path\n"
        "p = Path('/app/services/payment/app.py')\n"
        "s = p.read_text()\n"
        "gate = 'fault = FaultState()'\n"
        "marker = '    await fault.apply(SERVICE_NAME)'\n"
        "assert s.count(marker) == s.count(gate) == 1\n"
        f"helper = {source!r}\n"
        "s = s.replace(gate, 'import asyncio\\nimport time\\n' + helper + gate + "
        "'\\n_gradual_started = time.monotonic()')\n"
        "s = s.replace(marker, '    _gradual_ms = gradual_delay_ms(time.monotonic() - _gradual_started, "
        f"start_delay_s={values['start_delay_s']}, ramp_s={values['ramp_s']}, max_ms={values['max_ms']})\\n"
        "    if _gradual_ms:\\n        await asyncio.sleep(_gradual_ms / 1000)\\n' + marker, 1)\n"
        "p.write_text(s)\n"
    )


def gradual_dockerfile(base: str, **params) -> str:
    """Validate *base* and return the canonical gradual regression Dockerfile."""
    validate_base(base)
    values = _gradual_params(params)
    labels = ''.join(f' \\\n      sentinelops.dev/{key.replace("_", "-")}="{values[key]}"' for key in sorted(values))
    return (
        'ARG BASE\n'
        'FROM ${BASE}\n'
        'ARG BASE\n'
        'LABEL sentinelops.dev/variant="payment-gradual-regression" \\\n'
        '      sentinelops.dev/base-digest="${BASE}"' + labels + '\n'
        'RUN ' + json.dumps(['python', '-c', gradual_patch(**values)]) + '\n'
    )
