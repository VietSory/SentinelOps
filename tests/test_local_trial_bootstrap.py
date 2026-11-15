"""Local trial packaging must never combine mutually exclusive bootstraps."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import run_g6_trial, run_s2_h4


@pytest.mark.parametrize('runner,expected', [
    (run_s2_h4, ('001-local-roles.sql', '002-incident-store.sql',
                 '003-health-worker.sql', '004-incident-consumer.sql')),
    (run_g6_trial, ('001-local-roles.sql', '002-incident-store.sql',
                   '003-health-worker.sql', '004-incident-consumer.sql', '005-remediation.sql')),
])
def test_trial_stages_only_its_local_schema_profile(tmp_path, runner, expected):
    # Invoke the real staging path. Fake only the external Docker boundary;
    # actual deployment and optional quarantine SQL are present in this checkout.
    docker = Mock(side_effect=[b'exclusive-control-id', b'', b'127.0.0.1:15489\n'])
    sandbox = SimpleNamespace(root=tmp_path, name='exclusive-trial', docker=docker)
    owned = []
    runner.control_db(sandbox, 'fixture-postgres-image', owned)
    staged = tmp_path / 'db-init'
    assert sorted(path.name for path in staged.iterdir()) == list(expected)
    for filename in expected:
        assert (staged / filename).read_bytes() == (runner.PROJECT / 'infra/control' / filename).read_bytes()
    assert owned == ['exclusive-control-id']
    create = docker.call_args_list[0].args
    assert str(staged) + ':/docker-entrypoint-initdb.d:ro' in create
    assert not (tmp_path / 'db-bootstrap.env').exists()
