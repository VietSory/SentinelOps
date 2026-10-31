import json
from pathlib import Path

import pytest

from sentinelops.public_data import gamma_capabilities as module


def test_matrix_preserves_scope_and_zero_certified_support(monkeypatch):
    grouped = json.loads(Path('docs/reports/evidence/2026-10-26-gamma-grouped-dataset.json').read_text())
    source = {'training_data_policy_hash': grouped['training_data_policy_hash'], 'experiments': [
        {'raw_metrics': [{'source_path': 'fixture/container_cpu_usage_seconds_total', 'empty': False}],
         'processed_tables': [{'source_path': 'fixture/processed.csv'}]}]}
    monkeypatch.setattr(module, 'audit_gamma', lambda _: source)
    result = module.audit_capabilities(Path('fixture-only'), grouped)
    assert result['G2'] == result['G3'] == 'BLOCKED'
    assert result['healthy_certificates_generated'] == result['precursor_labels_generated'] == result['fitted_models'] == 0
    matrix = {r['feature']: r for r in result['compatibility_matrix']}
    assert matrix['cpu_cores']['status'] == 'VERIFIED_DERIVED'
    assert matrix['memory_bytes']['status'] == 'NOT_AVAILABLE'
    assert matrix['http_error_rate']['status'] == 'SEMANTICALLY_INCOMPATIBLE'
    assert matrix['native_rpc_timing']['status'] == 'AVAILABLE_BUT_UNVERIFIED'
    assert all(c['available'] == 0 for c in result['failed_conditions'])
    source['training_data_policy_hash'] = 'forged'
    with pytest.raises(ValueError, match='different frozen policies'):
        module.audit_capabilities(Path('fixture-only'), grouped)
