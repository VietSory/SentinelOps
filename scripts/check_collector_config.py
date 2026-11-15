"""Validate the rendered AWS collector with a real, isolated collector binary.

Only config validation runs, never exporters. Network is disabled and no AWS
credential/token/host directory is supplied to either validation container.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess

import yaml

from scripts.render_collector_manifests import CHECK_IMAGE, render
from sentinelops.remediation.domain import image_digest


def check(image: str = CHECK_IMAGE, *, pull: bool = False) -> dict:
    image_digest(image)
    if pull:
        subprocess.run(['docker', 'pull', image], check=True, timeout=180)
    documents = render(image, region='eu-central-1', environment='dev',
                       exporter_role_arn='arn:aws:iam::123456789012:role/sentinelops-telemetry-exporter',
                       trace_sampling_percentage=10)
    config = yaml.safe_load(next(d for d in documents if d['kind'] == 'ConfigMap')['data']['config.yaml'])

    def validate(candidate):
        env = dict(os.environ)
        env['OTELCOL_CONFIG'] = yaml.safe_dump(candidate)
        return subprocess.run([
            'docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
            '--user', '10001:10001', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--pids-limit', '64', '--memory', '256m', '--cpus', '1',
            '--env', 'OTELCOL_CONFIG', '--env', 'POD_UID=software-fixture',
            '--env', 'AWS_EC2_METADATA_DISABLED=true', image,
            'validate', '--config=env:OTELCOL_CONFIG',
        ], env=env, capture_output=True, text=True, timeout=60)

    accepted = validate(config)
    if accepted.returncode:
        raise RuntimeError('collector rejected rendered config: ' + accepted.stdout + accepted.stderr)
    broken = copy.deepcopy(config)
    broken['exporters']['awsxray']['__invalid_config_probe__'] = True
    rejected = validate(broken)
    if rejected.returncode == 0 or '__invalid_config_probe__' not in rejected.stdout + rejected.stderr:
        raise RuntimeError('collector did not demonstrate rejection of the invalid exporter setting')
    return {'status': 'PASS', 'scope': 'collector-configuration-only', 'image': image,
            'rendered_config_valid': True, 'invalid_exporter_setting_rejected': True,
            'container_network': 'none', 'aws_credentials_supplied': False,
            'exporters_started': False, 'deployment_verified': False, 'H3_accepted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=CHECK_IMAGE)
    parser.add_argument('--pull', action='store_true', help='Fetch the pinned image before isolated validation')
    args = parser.parse_args()
    print(json.dumps(check(args.image, pull=args.pull), indent=2))


if __name__ == '__main__':
    main()
