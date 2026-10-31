"""Frozen source policy must reject silent substitutions and target changes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from sentinelops.contracts import validate_payload


def policy():
    root = Path(__file__).resolve().parents[1]
    return json.loads((root / "configs/training_data_contract.json").read_text())


def test_approved_training_data_configuration_is_contract_valid():
    validate_payload("training-data-policy.schema.json", policy())


@pytest.mark.parametrize("change", ["third-source", "gamma-version", "rcaeval-fitting", "post-fault-target", "implicit-red", "test-tuning"])
def test_frozen_contract_rejects_changes_that_would_invalidate_experiment_claims(change):
    value = deepcopy(policy())
    if change == "third-source":
        value["sources"]["smd"] = {"role": "primary-training"}
    elif change == "gamma-version":
        value["sources"]["gamma"]["version"] = 2
    elif change == "rcaeval-fitting":
        value["sources"]["rcaeval"]["ml_fitting_allowed"] = True
    elif change == "post-fault-target":
        value["risk_target"] = "current-bottleneck"
    elif change == "implicit-red":
        value["feature_mapping"] = "rename-rpc-as-http"
    else:
        value["on_gate_failure"] = "tune-on-final-test"
    with pytest.raises(ValueError):
        validate_payload("training-data-policy.schema.json", value)


def test_adr014_current_authority_preserves_historical_source_bytes():
    from sentinelops.public_data.policy import load_policy, load_training_policy, policy_hash, require_current_training_source
    assert policy_hash(load_policy()) == 'ef0eb1b1c95a67faf424501e657c5e6530d49a1d18af7cbf59f9c07740afa21f'
    current = load_training_policy()
    assert current['authorizing_adr'] == 'ADR-014' and current['risk_horizon_seconds'] == 300
    require_current_training_source('local_http', current)
    for source in ('gamma', 'rcaeval', 'smd', 'previous-local-pilots'):
        with pytest.raises(ValueError, match='prospective registered'):
            require_current_training_source(source, current)


@pytest.mark.parametrize('field,value', [('risk_horizon_seconds', 60), ('risk_target', 'fault-occurrence'),
    ('source_certificates', 'caller-pass-flags'), ('final_access', 'open-for-tuning'),
    ('healthy_windows_per_service', {'train': 3, 'calibration': 1})])
def test_v2_authorization_does_not_weaken_evidence_or_target(field, value):
    from sentinelops.public_data.policy import load_training_policy
    current = load_training_policy()
    current[field] = value
    with pytest.raises(ValueError):
        validate_payload('training-data-policy-v2.schema.json', current)
