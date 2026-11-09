"""Shared serving-release ledger versions.

The release builder for the ADR-016 Alibaba bundle lives in
`sentinelops.ml.alibaba_release`. The earlier per-service local release path was
retired with local-data training (ADR-016, owner approval 2026-11-09); its
records stay readable in git history.
"""
from __future__ import annotations

from sentinelops.telemetry.core import stable_id

PARITY_VERSION = "model-source-parity/v1"
PARITY_VERIFICATION_VERSION = "model-source-parity-check/v1"
APPROVAL_VERSION = "model-release-approval/v1"
RELEASE_VERSION = "validated-model-release/v1"


def parity_verification_ref(verification):
    return f"artifact://manifests/{stable_id(verification)}.json"
