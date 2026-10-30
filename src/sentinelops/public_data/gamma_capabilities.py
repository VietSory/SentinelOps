"""Source-bounded capability and blocker report; never grants admission."""
from __future__ import annotations

from sentinelops.ml.readiness import audit_readiness
from sentinelops.public_data.gamma_audit import audit_gamma


def audit_capabilities(manifest_path, grouped_report: dict) -> dict:
    source = audit_gamma(manifest_path)  # revalidate pinned source bytes before interpreting them
    readiness = audit_readiness(grouped_report)  # historical aggregate, not a new cohort read
    if source['training_data_policy_hash'] != readiness['training_data_policy_hash']:
        raise ValueError('source and aggregate use different frozen policies')
    refs = sorted(m['source_path'] for e in source['experiments'] for m in e['raw_metrics'] if not m['empty'])
    processed = sorted(t['source_path'] for e in source['experiments'] for t in e['processed_tables'])
    matrix = []
    def add(feature, status, source_refs, derivation, limitation):
        matrix.append(dict(feature=feature, status=status, source_paths=source_refs,
                           derivation=derivation, limitation=limitation))
    for feature, suffix, formula, note in (
        ('cpu_cores', 'container_cpu_usage_seconds_total', 'delta cumulative CPU seconds / covered seconds', 'cores, not CPU percentage; reset/gap intervals excluded'),
        ('memory_bytes', 'container_memory_usage_bytes', 'time-weighted left-held bytes', 'bounded gauge age, not memory utilization without verified capacity'),
        ('network_bytes_per_second', 'bytes_total', 'delta cumulative bytes / covered seconds', 'not HTTP request rate; reset/gap intervals excluded'),
    ):
        matching = [p for p in refs if p.endswith(suffix)]
        add(feature, 'VERIFIED_DERIVED' if matching else 'NOT_AVAILABLE', matching, formula, note)
    add('native_resource_entity', 'VERIFIED_DIRECT', refs, None, 'native filename identities; no alias to Checkout/Payment/Inventory')
    add('cpu_memory_utilization_percent', 'SEMANTICALLY_INCOMPATIBLE', refs, None, 'cores/bytes alone do not establish capacity-normalized utilization')
    for feature in ('request_rate', 'http_error_rate', 'http_latency_p50_p95', 'latency_slope', 'error_rate_growth'):
        add(feature, 'SEMANTICALLY_INCOMPATIBLE', processed, None,
            'RPC start/latency labels lack verified HTTP status denominator, sampling completeness, units and live service mapping')
    add('native_rpc_timing', 'AVAILABLE_BUT_UNVERIFIED', processed, None, 'numeric timing exists; units/mapping and sampling not certified by this audit')
    for feature in ('connection_utilization', 'timeout_count', 'database_error_count', 'log_pattern_frequency'):
        add(feature, 'NOT_AVAILABLE', [], None, 'not in the acquired source scope; archive filenames do not prove usable observations')
    requirements = []
    for service, counts in readiness['certified_business_service_health'].items():
        for role, floor, groups in (('train', 300, 3), ('calibration', 100, 2)):
            requirements.append(dict(condition=f'{service}/{role}/certified-healthy-windows', available=counts[role],
                required=floor, missing=floor-counts[role], independent_groups_available=counts[role+'_runs'],
                independent_groups_required=groups, repairable_from_verified_bytes=False,
                missing_evidence='complete workload traffic + objective SLO health + native service identity + independent source groups',
                policy_change_required_if_new_source=True))
    for condition, evidence in (
        ('verified-pre-SLO-precursor-runs', 'independent gradual runs, outcome SLO clocks and causal pre-outcome features; intervention time is not outcome time'),
        ('live-feature-source-parity', 'same feature units, service identities, traffic completeness and event-time definitions'),
        ('source-backed-admission-verifier', 'an accepted verifier binding healthy/precursor certificates to source observations, not caller flags'),
    ):
        requirements.append(dict(condition=condition, available=0, required='verified evidence before admission',
            missing_evidence=evidence, repairable_from_verified_bytes=False,
            policy_change_required_if_new_source=True))
    return dict(report_version='gamma-capability-blockers/v1', source_audit=source,
        historical_grouped_readiness=readiness, compatibility_matrix=matrix, failed_conditions=requirements,
        healthy_certificates_generated=0, precursor_labels_generated=0, fitted_models=0,
        G2='BLOCKED', G3='BLOCKED', activation_allowed=False, holdout_opened=False,
        limitations=['This is a bounded 142-file capability audit plus a complete filename catalogue and previously measured aggregates.',
                    'Unacquired full logs/traces cannot be declared semantically absent from the entire dataset.',
                    'Reference/no-intervention and low resource usage do not certify healthy workload.',
                    'No independent experiment count is inferred from overlapping windows.'])
