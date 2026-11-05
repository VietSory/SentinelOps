"""Validate operator Prometheus rules with the actually deployed Prometheus.

Runs `promtool` from the same pinned image the Compose stack runs, so rules are
never validated by a different version than the one that will load them. Checks
that every referenced rule file is really bind-mounted read-only at its declared
container path, that the rendered configuration loads, and that every rule file
has passing unit tests.

This validates rule evaluation only. It is not source, model, RCA or remediation
evidence, and it starts no stack and reads no database.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

import yaml

PROJECT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT/'observability/prometheus.yml'
RULES = PROJECT/'observability/rules'
TESTS = RULES/'tests'
CONTAINER_CONFIG = '/etc/prometheus/prometheus.yml'


def prometheus_image(compose: dict) -> str:
    """Use the Compose image so rules match the loader that will run them."""
    image = compose['services']['prometheus']['image']
    if not isinstance(image, str) or ':' not in image or image.endswith(':latest'):
        raise ValueError('rule validation requires an explicitly pinned Prometheus image')
    return image


def mounted_rule_files(compose: dict, config: dict, *, project: Path = PROJECT) -> dict[str, Path]:
    """Map each declared container rule path to its owned read-only host file."""
    binds = {}
    for mount in compose['services']['prometheus'].get('volumes', []):
        if not isinstance(mount, str):
            raise ValueError('Prometheus binds must be short-syntax strings for the deployment lock')
        parts = mount.split(':')
        if not parts[0].startswith('./'):
            continue
        if len(parts) not in (2, 3):
            raise ValueError('unsupported Prometheus bind syntax: ' + mount)
        # An omitted mode means read-write; record it rather than skipping, so a
        # writable configuration bind is reported as writable, not as missing.
        source, target = parts[0], parts[1]
        binds[target] = ((project/source).resolve(), parts[2] if len(parts) == 3 else 'rw')

    if CONTAINER_CONFIG in binds and binds[CONTAINER_CONFIG][1] != 'ro':
        raise ValueError('the Prometheus configuration bind must be read-only')

    declared = config.get('rule_files') or []
    resolved = {}
    for entry in declared:
        if any(character in entry for character in '*?['):
            raise ValueError('rule_files must name explicit files so every file is hashed: ' + entry)
        if entry not in binds:
            raise ValueError('rule file is referenced but not bind-mounted; Prometheus would fail to start: ' + entry)
        source, mode = binds[entry]
        if mode != 'ro':
            raise ValueError('a Prometheus rule bind must be read-only for the deployment lock: ' + entry)
        if not source.is_file() or not source.is_relative_to(project):
            raise ValueError('rule file bind must be an owned regular file: ' + entry)
        resolved[entry] = source
    if not resolved:
        raise ValueError('no Prometheus rule files are configured')
    return resolved


def promtool(image: str, mounts: list[tuple[Path, str]], *arguments: str) -> str:
    command = ['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'promtool']
    for source, target in mounts:
        command += ['-v', str(source)+':'+target+':ro']
    command += [image, *arguments]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise SystemExit('promtool '+' '.join(arguments)+' failed:\n'+completed.stdout+completed.stderr)
    return completed.stdout


def verify_running(base_url: str, expected: set[str], *, timeout: float = 90.) -> dict:
    """Confirm a running Prometheus loaded and actually evaluated every rule.

    A rule reports health `unknown` until its first evaluation, and the group
    interval means that is the normal state immediately after startup. Only
    `err` is a real failure, so poll until every rule has evaluated.
    """
    deadline, pending, last = time.monotonic()+timeout, None, None
    while time.monotonic() < deadline:
        with urllib.request.urlopen(base_url.rstrip('/')+'/api/v1/rules', timeout=10) as response:
            body = json.load(response)
        if body.get('status') != 'success':
            raise ValueError('Prometheus rules API did not succeed: '+json.dumps(body)[:300])
        rules = [rule for group in body['data']['groups'] for rule in group['rules']]
        last = {rule['name']: rule['health'] for rule in rules}
        failed = [(rule['name'], rule.get('lastError')) for rule in rules if rule['health'] == 'err']
        if failed:
            raise ValueError('Prometheus reported failing rules: '+json.dumps(failed))
        missing = expected - set(last)
        if missing:
            raise ValueError('the running Prometheus did not load: '+', '.join(sorted(missing)))
        pending = sorted(name for name, health in last.items() if health != 'ok')
        if not pending:
            return {'rules_loaded': sorted(last), 'all_evaluated': True,
                    'groups': len(body['data']['groups'])}
        time.sleep(3)
    raise ValueError('rules never finished a first evaluation within '
                     + str(int(timeout))+'s: '+json.dumps(pending or last))


def declared_rule_names() -> set[str]:
    names = set()
    for path in sorted(RULES.glob('*.yml')):
        for group in yaml.safe_load(path.read_text())['groups']:
            for rule in group['rules']:
                names.add(rule.get('record') or rule['alert'])
    return names


def exercise(output: Path | None, base_url: str | None = None) -> dict:
    compose = yaml.safe_load((PROJECT/'docker-compose.yml').read_text())
    image = prometheus_image(compose)
    rule_files = mounted_rule_files(compose, yaml.safe_load(CONFIG.read_text()))

    # Load the real configuration with the rule files at their declared paths, so
    # a path/mount mismatch fails here instead of at container start.
    mounts = [(CONFIG, CONTAINER_CONFIG)] + [(source, target) for target, source in rule_files.items()]
    config_output = promtool(image, mounts, 'check', 'config', CONTAINER_CONFIG)

    test_files = sorted(TESTS.glob('*.test.yml'))
    if not test_files:
        raise ValueError('operator rules require promtool unit tests')
    covered = set()
    for path in test_files:
        declared = yaml.safe_load(path.read_text()).get('rule_files') or []
        for entry in declared:
            covered.add((path.parent/entry).resolve())
    missing = sorted(str(source.relative_to(PROJECT)) for source in rule_files.values() if source not in covered)
    if missing:
        raise ValueError('rule files without unit tests: '+', '.join(missing))

    # Mount the rules tree so each test file resolves its relative rule_files.
    test_output = promtool(image, [(RULES, '/rules')],
                           'test', 'rules', *['/rules/tests/'+path.name for path in test_files])

    result = {'status': 'PASS', 'scope': 'prometheus-rule-evaluation-only',
              'prometheus_image': image,
              'rule_files': {target: str(source.relative_to(PROJECT)) for target, source in rule_files.items()},
              'unit_test_files': [str(path.relative_to(PROJECT)) for path in test_files],
              'config_check': config_output.strip().splitlines(),
              'unit_tests': test_output.strip().splitlines(),
              'source_evidence': False, 'model_evidence': False,
              'rca_quality_evidence': False, 'actions_allowed': False}
    if base_url:
        try:
            result['runtime'] = verify_running(base_url, declared_rule_names())
        except (urllib.error.URLError, OSError) as error:
            raise SystemExit('could not reach Prometheus at '+base_url+': '+str(error))
    if output is not None:
        write_report(result, output)
    return result


def write_report(result: dict, output: Path) -> None:
    """Never overwrite an earlier report, and create the directory it needs."""
    if output.exists():
        raise ValueError('rule validation output must be new')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--base-url', help='Verify a running Prometheus, for example http://127.0.0.1:19090')
    arguments = parser.parse_args()
    print(json.dumps(exercise(arguments.output, arguments.base_url), indent=2), flush=True)
    print('PROMETHEUS RULE VALIDATION: PASS', flush=True)


if __name__ == '__main__':
    main()
