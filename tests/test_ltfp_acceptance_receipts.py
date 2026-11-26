import pytest

from scripts.verify_ltfp_acceptance import _test_results


def test_receipt_requires_every_check_without_skips_or_failures():
    checks = {'A01': ['tests/test_example.py::test_boundary']}
    success = b'<testsuites><testsuite><testcase classname="tests.test_example" name="test_boundary[one]"/></testsuite></testsuites>'
    assert _test_results(success, checks) == 1
    with pytest.raises(ValueError, match='missing'):
        _test_results(success, {'A01': ['tests/test_example.py::test_other']})
    for tag in ('skipped', 'failure', 'error'):
        xml = success.replace(b'/>', f'><{tag}/></testcase>'.encode())
        with pytest.raises(ValueError, match='failed or skipped'):
            _test_results(xml, checks)
