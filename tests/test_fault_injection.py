"""Fault endpoints require explicit enablement and a permitted environment."""

import pytest
from fastapi import HTTPException

from services.common.faults import require_fault_injection


@pytest.mark.parametrize("environment", [None, "local", "test", "dev"])
def test_fault_injection_disabled_by_default(monkeypatch, environment):
    monkeypatch.delenv("ENABLE_FAULT_INJECTION", raising=False)
    monkeypatch.delenv("ALLOW_DEV_FAULT_INJECTION", raising=False)
    monkeypatch.delenv("DEPLOYMENT_ENVIRONMENT", raising=False)
    if environment is not None:
        monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", environment)
    with pytest.raises(HTTPException) as error:
        require_fault_injection()
    assert error.value.status_code == 404
    assert error.value.detail == "fault injection disabled"


@pytest.mark.parametrize("environment", [None, "", "production", "prod", "staging", "aws-dev", "DEV", "dev "])
@pytest.mark.parametrize("enabled", ["true", "false"])
@pytest.mark.parametrize("dev_opt_in", ["true", "false"])
def test_fault_injection_rejects_unlisted_environment(monkeypatch, environment, enabled, dev_opt_in):
    monkeypatch.delenv("DEPLOYMENT_ENVIRONMENT", raising=False)
    if environment is not None:
        monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", environment)
    monkeypatch.setenv("ENABLE_FAULT_INJECTION", enabled)
    monkeypatch.setenv("ALLOW_DEV_FAULT_INJECTION", dev_opt_in)
    with pytest.raises(HTTPException) as error:
        require_fault_injection()
    assert error.value.status_code == 404


@pytest.mark.parametrize("environment", ["local", "test", "dev"])
@pytest.mark.parametrize("enabled", [None, "false", "1", "yes", "true ", "true"])
@pytest.mark.parametrize("dev_opt_in", [None, "false", "1", "yes", "true ", "true", "TRUE"])
def test_fault_injection_enablement_matrix(monkeypatch, environment, enabled, dev_opt_in):
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", environment)
    for name, value in (
        ("ENABLE_FAULT_INJECTION", enabled),
        ("ALLOW_DEV_FAULT_INJECTION", dev_opt_in),
    ):
        monkeypatch.delenv(name, raising=False)
        if value is not None:
            monkeypatch.setenv(name, value)
    allowed = enabled == "true" and (
        environment in ("local", "test") or dev_opt_in in ("true", "TRUE")
    )
    if allowed:
        assert require_fault_injection() is None
    else:
        with pytest.raises(HTTPException) as error:
            require_fault_injection()
        assert error.value.status_code == 404
