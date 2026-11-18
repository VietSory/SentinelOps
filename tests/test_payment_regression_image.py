import json
from pathlib import Path

import pytest

from scripts.check_payment_regression_image import DOCKERFILE, check
from scripts.payment_regression import PATCH, dockerfile, validate_base
from scripts.run_s2_h4 import bad_image


BASE = '123456789012.dkr.ecr.us-east-1.amazonaws.com/sentinelops/application@sha256:' + 'a' * 64


class Sandbox:
    def __init__(self, root):
        self.root = root
        self.port = 5001
        self.nodes = ('kind-control-plane',)
        self.calls = []

    def docker(self, *args):
        self.calls.append(args)
        if args[:2] == ('image', 'inspect'):
            return json.dumps([
                f'localhost:{self.port}/sentinelops-business@sha256:' + 'b' * 64
            ]).encode()
        return b''


def test_tracked_dockerfile_equals_local_bad_image_output_for_fixture_base(tmp_path):
    sandbox = Sandbox(tmp_path)
    bad_image(sandbox, BASE)

    local = tmp_path / 'payment-image-context' / 'Dockerfile'
    assert local.read_text() == DOCKERFILE.read_text() == dockerfile(BASE)
    assert ('build', '--build-arg', 'BASE=' + BASE, '-f', str(local), '-t',
            'localhost:5001/sentinelops-business:s2-regression', str(local.parent)) in sandbox.calls
    check(BASE)


@pytest.mark.parametrize('base', [
    '123456789012.dkr.ecr.us-east-1.amazonaws.com/sentinelops/application:latest',
    '123456789012.dkr.ecr.us-east-1.amazonaws.com/sentinelops/other@sha256:' + 'a' * 64,
    '123456789012.dkr.ecr.us-east-1.amazonaws.com/sentinelops/application@sha256:' + 'a' * 63,
])
def test_base_must_be_a_digest_in_the_application_repository(base):
    with pytest.raises(ValueError, match='sentinelops/application'):
        validate_base(base)
    with pytest.raises(ValueError, match='sentinelops/application'):
        dockerfile(base)


def test_patch_anchors_exist_in_payment_source_at_head():
    source = (Path(__file__).resolve().parents[1] / 'services' / 'payment' / 'app.py').read_text()
    assert "gate = 'fault = FaultState()'" in PATCH
    assert "marker = '    await fault.apply(SERVICE_NAME)'" in PATCH
    assert 'fault = FaultState()' in source
    assert '    await fault.apply(SERVICE_NAME)' in source
