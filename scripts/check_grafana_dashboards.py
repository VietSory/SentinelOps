"""Validate the provisioned Grafana dashboards against the real metric surface.

Checks that every panel query references a metric or recording rule that actually
exists, that no query reads source availability without the decision guard, that
missing data is never presented as healthy, and that the console is provisioned
read-only. Optionally verifies a running Grafana actually loaded the dashboard.

This validates a console definition. It is not source, model or RCA evidence,
and no dashboard grants model activation or remediation authority.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

import yaml

PROJECT = Path(__file__).resolve().parents[1]
GRAFANA = PROJECT/'observability/grafana'
DASHBOARDS = GRAFANA/'dashboards'
RULES = PROJECT/'observability/rules'
DATASOURCE_UID = 'sentinelops-prometheus'

# Raw gauge whose value is only meaningful for a replica that committed a local
# decision; reading it unguarded reports "source down" for an undecided replica.
GUARDED_METRIC = 'sentinelops_worker_source_available'
DECIDED_RULE = 'sentinelops:worker_source_available:decided'
GUARD_TOKENS = ('sentinelops_worker_has_local_decision', DECIDED_RULE)


def exported_metric_names() -> set[str]:
    """The Prometheus-exported names, taken from the existing evidence module."""
    from scripts.worker_metrics_evidence import NAMES
    names = set(NAMES.values())
    # Incident-worker instruments, exported with the same suffix rules.
    names |= {'sentinelops_incident_has_local_progress', 'sentinelops_incident_groups_total',
              'sentinelops_rca_abstentions_total'}
    return names


def recording_rule_names() -> set[str]:
    names = set()
    for path in sorted(RULES.glob('*.yml')):
        for group in yaml.safe_load(path.read_text())['groups']:
            for rule in group['rules']:
                if 'record' in rule:
                    names.add(rule['record'])
    return names


def dashboards() -> dict[str, dict]:
    found = {path.name: json.loads(path.read_text()) for path in sorted(DASHBOARDS.glob('*.json'))}
    if not found:
        raise ValueError('no provisioned Grafana dashboard found')
    return found


def queries(dashboard: dict) -> list[tuple[dict, str]]:
    pairs = []
    for panel in dashboard['panels']:
        for target in panel.get('targets', []):
            if 'expr' in target:
                pairs.append((panel, target['expr']))
    return pairs


def referenced_metrics(expression: str) -> set[str]:
    return set(re.findall(r'\bsentinelops[a-z0-9_:]*\b', expression))


def assert_queries_resolve(all_dashboards: dict[str, dict], known: set[str]) -> list[str]:
    checks, total = [], 0
    for name, dashboard in all_dashboards.items():
        for panel, expression in queries(dashboard):
            total += 1
            unknown = referenced_metrics(expression) - known
            if unknown:
                raise ValueError(name+' panel '+repr(panel['title'])+' queries unknown series: '+', '.join(sorted(unknown)))
        for variable in dashboard.get('templating', {}).get('list', []):
            unknown = referenced_metrics(variable.get('query', '')) - known
            if unknown:
                raise ValueError(name+' variable '+variable['name']+' queries unknown series: '+', '.join(sorted(unknown)))
    checks.append(f'all {total} panel queries reference existing metrics or recording rules')
    return checks


def assert_source_availability_is_guarded(all_dashboards: dict[str, dict]) -> list[str]:
    presented = 0
    for name, dashboard in all_dashboards.items():
        for panel, expression in queries(dashboard):
            metrics = referenced_metrics(expression)
            # The recording rule is itself the guarded form; a raw read is only
            # acceptable when the expression joins the decision gauge.
            if DECIDED_RULE in metrics:
                presented += 1
                continue
            if GUARDED_METRIC not in metrics:
                continue
            if not any(token in expression for token in GUARD_TOKENS):
                raise ValueError(name+' panel '+repr(panel['title'])
                                 + ' reads source availability without the decision guard')
            presented += 1
    if not presented:
        raise ValueError('expected a panel showing source eligibility through the guarded series')
    return [f'{presented} source-availability panel(s) read only decided replicas']


def assert_missing_data_is_not_healthy(all_dashboards: dict[str, dict]) -> list[str]:
    """An absent series must never render as zero or as a healthy state."""
    checked = 0
    for name, dashboard in all_dashboards.items():
        for panel in dashboard['panels']:
            if panel['type'] not in ('stat', 'state-timeline', 'gauge'):
                continue
            defaults = panel.get('fieldConfig', {}).get('defaults', {})
            no_value = defaults.get('noValue')
            if no_value is None:
                raise ValueError(name+' panel '+repr(panel['title'])+' must state what absent data means')
            if re.fullmatch(r'\s*0+\s*', str(no_value)) or str(no_value).lower() in ('ok', 'healthy', 'up', 'ready'):
                raise ValueError(name+' panel '+repr(panel['title'])+' presents missing data as healthy: '+str(no_value))
            checked += 1
            # A state panel must label its values, so state is never colour alone.
            if panel['type'] in ('stat', 'state-timeline') and defaults.get('mappings'):
                for mapping in defaults['mappings']:
                    for value in mapping.get('options', {}).values():
                        if isinstance(value, dict) and not value.get('text'):
                            raise ValueError(name+' panel '+repr(panel['title'])+' maps a value to colour without text')
    return [f'{checked} state panel(s) declare absent data explicitly and label every mapped value']


def assert_single_axis(all_dashboards: dict[str, dict]) -> list[str]:
    """Two y-scales in one panel is never an accepted presentation."""
    for name, dashboard in all_dashboards.items():
        for panel in dashboard['panels']:
            for override in panel.get('fieldConfig', {}).get('overrides', []):
                for property_ in override.get('properties', []):
                    if property_.get('id') == 'custom.axisPlacement' and property_.get('value') == 'right':
                        raise ValueError(name+' panel '+repr(panel['title'])+' introduces a second y-axis')
                    if property_.get('id') == 'unit':
                        raise ValueError(name+' panel '+repr(panel['title'])
                                         + ' overrides the unit per series, which puts two measures on one axis')
    return ['no panel uses a second y-axis or a per-series unit override']


def assert_legend_present(all_dashboards: dict[str, dict]) -> list[str]:
    for name, dashboard in all_dashboards.items():
        for panel in dashboard['panels']:
            if panel['type'] not in ('timeseries', 'state-timeline'):
                continue
            legend = panel.get('options', {}).get('legend', {})
            if not legend.get('showLegend'):
                raise ValueError(name+' panel '+repr(panel['title'])+' must show a legend so identity is not colour alone')
    return ['every plotted panel shows a legend']


def assert_provisioned_read_only(all_dashboards: dict[str, dict]) -> list[str]:
    checks = []
    datasource = yaml.safe_load((GRAFANA/'provisioning/datasources/prometheus.yaml').read_text())
    entry = next(item for item in datasource['datasources'] if item['uid'] == DATASOURCE_UID)
    if entry.get('editable') is not False:
        raise ValueError('the provisioned datasource must not be editable in the UI')
    if entry['type'] != 'prometheus':
        raise ValueError('the dashboard datasource must be Prometheus')
    checks.append('datasource provisioned read-only')

    provider = yaml.safe_load((GRAFANA/'provisioning/dashboards/sentinelops.yaml').read_text())['providers'][0]
    if provider.get('allowUiUpdates') is not False or provider.get('disableDeletion') is not True:
        raise ValueError('dashboards must come from the committed file, not from UI edits')
    checks.append('dashboards provisioned from the committed file, UI updates disabled')

    for name, dashboard in all_dashboards.items():
        if dashboard.get('editable') is not False:
            raise ValueError(name+' must be marked not editable')
        if not dashboard.get('description'):
            raise ValueError(name+' must describe its scope')
        for panel, _ in queries(dashboard):
            uid = panel.get('datasource', {}).get('uid')
            if uid != DATASOURCE_UID:
                raise ValueError(name+' panel '+repr(panel['title'])+' points at datasource '+repr(uid))
    checks.append('all panels bound to the provisioned datasource')
    return checks


def assert_console_is_not_production_auth() -> list[str]:
    compose = yaml.safe_load((PROJECT/'docker-compose.yml').read_text())
    service = compose['services']['grafana']
    environment = service['environment']

    def disabled(key: str) -> bool:
        # Compose env values may be YAML booleans or quoted strings.
        return str(environment.get(key, '')).strip().lower() in ('false', 'no', '0', '')

    if environment.get('GF_AUTH_ANONYMOUS_ORG_ROLE') != 'Viewer':
        raise ValueError('anonymous Grafana access must be Viewer only')
    if not disabled('GF_USERS_ALLOW_SIGN_UP'):
        raise ValueError('Grafana sign-up must be disabled')
    if not disabled('GF_USERS_VIEWERS_CAN_EDIT'):
        raise ValueError('Grafana viewers must not be able to edit')
    if not disabled('GF_AUTH_BASIC_ENABLED'):
        raise ValueError('the sandbox console must not pretend to offer real authentication')
    for port in service['ports']:
        if not str(port).startswith('127.0.0.1:'):
            raise ValueError('the Grafana console must be published on loopback only')
    binds = [mount for mount in service['volumes'] if mount.startswith('./')]
    if not binds:
        raise ValueError('Grafana provisioning must be bind-mounted from the repository')
    for mount in binds:
        parts = mount.split(':')
        if len(parts) != 3 or parts[2] != 'ro':
            raise ValueError('a Grafana provisioning bind must be read-only: '+mount)
        if not (PROJECT/parts[0]).is_file():
            raise ValueError('Grafana provisioning binds must be individual files: '+mount)
    image = service['image']
    if ':' not in image or image.endswith(':latest'):
        raise ValueError('the Grafana image must be explicitly pinned')
    return [f'console pinned to {image}, loopback only, anonymous Viewer, {len(binds)} read-only file binds']


def verify_running(base_url: str, all_dashboards: dict[str, dict]) -> dict:
    """Confirm a running Grafana loaded the provisioned dashboard and datasource."""
    def get(path):
        with urllib.request.urlopen(base_url.rstrip('/')+path, timeout=10) as response:
            return json.load(response)

    health = get('/api/health')
    if health.get('database') != 'ok':
        raise ValueError('running Grafana is not healthy: '+json.dumps(health))
    observed = {}
    for dashboard in all_dashboards.values():
        uid = dashboard['uid']
        loaded = get('/api/dashboards/uid/'+uid)
        panels = loaded['dashboard']['panels']
        if len(panels) != len(dashboard['panels']):
            raise ValueError('running Grafana shows a different panel count for '+uid)
        if loaded['meta'].get('provisioned') is not True:
            raise ValueError(uid+' is not reported as provisioned')
        observed[uid] = {'version': health.get('version'), 'panels': len(panels),
                         'provisioned': True, 'title': loaded['dashboard']['title']}
    sources = get('/api/datasources')
    if not any(item['uid'] == DATASOURCE_UID for item in sources):
        raise ValueError('the provisioned datasource is absent from the running Grafana')

    # Static name checking cannot catch an invalid PromQL function or a bad
    # aggregation, so evaluate every expression on the real Prometheus through
    # Grafana's datasource proxy.
    evaluated = 0
    for name, dashboard in all_dashboards.items():
        for panel, expression in queries(dashboard):
            concrete = expression.replace('$environment', 'local').replace('$job', '.*')
            encoded = urllib.parse.quote(concrete, safe='')
            path = '/api/datasources/proxy/uid/'+DATASOURCE_UID+'/api/v1/query?query='+encoded
            try:
                body = get(path)
            except urllib.error.HTTPError as error:
                raise ValueError(name+' panel '+repr(panel['title'])+' is not valid PromQL: '
                                 + error.read().decode('utf-8', 'replace')[:300]) from error
            if body.get('status') != 'success':
                raise ValueError(name+' panel '+repr(panel['title'])+' failed evaluation: '+json.dumps(body)[:300])
            evaluated += 1
    return {'grafana_version': health.get('version'), 'dashboards': observed,
            'datasource_present': True, 'expressions_evaluated_on_prometheus': evaluated}


def exercise(output: Path | None, base_url: str | None) -> dict:
    all_dashboards = dashboards()
    known = exported_metric_names() | recording_rule_names()

    checks: list[str] = []
    checks += assert_queries_resolve(all_dashboards, known)
    checks += assert_source_availability_is_guarded(all_dashboards)
    checks += assert_missing_data_is_not_healthy(all_dashboards)
    checks += assert_single_axis(all_dashboards)
    checks += assert_legend_present(all_dashboards)
    checks += assert_provisioned_read_only(all_dashboards)
    checks += assert_console_is_not_production_auth()

    result = {'status': 'PASS', 'scope': 'grafana-console-definition',
              'dashboards': sorted(all_dashboards), 'known_series': len(known),
              'checks': checks, 'production_auth': False,
              'business_slo_health_evidence': False, 'model_evidence': False,
              'rca_quality_evidence': False, 'actions_allowed': False}
    if base_url:
        try:
            result['runtime'] = verify_running(base_url, all_dashboards)
            result['checks'].append('running Grafana loaded every provisioned dashboard and the datasource')
        except (urllib.error.URLError, OSError) as error:
            raise SystemExit('could not reach Grafana at '+base_url+': '+str(error))
    if output is not None:
        if output.exists():
            raise ValueError('dashboard validation output must be new')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--base-url', help='Verify a running Grafana, for example http://127.0.0.1:13000')
    arguments = parser.parse_args()
    print(json.dumps(exercise(arguments.output, arguments.base_url), indent=2), flush=True)
    print('GRAFANA DASHBOARD VALIDATION: PASS', flush=True)


if __name__ == '__main__':
    main()
