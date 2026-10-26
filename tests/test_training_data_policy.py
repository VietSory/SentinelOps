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
