"""Development evaluation only: truth never enters correlation or RCA inputs."""
from sentinelops.contracts import utc_time,validate_payload
from sentinelops.experiments.labels import SLOConfig,measure_slo
from sentinelops.telemetry.core import BUSINESS_ROUTES


def warmup_report(records,run,minimum_full_buckets):
    result={}
    for service in BUSINESS_ROUTES:
        measured=measure_slo(records,service,SLOConfig(**run['parameters']['slo']),
            start_at=utc_time(run['started_at']),end_at=utc_time(run['fault_start_at']))
        eligible=sum(bucket['eligible'] for bucket in measured['buckets'])
        result[service]={'eligible_request_buckets':eligible,'sustained_breach_at':measured['first_violation_at'],
                         'passed':eligible>=minimum_full_buckets and measured['first_violation_at'] is None}
    return result


def selected_development_case(run,protocol):
    if (protocol.get('protocol_version')!='s5-development/v1' or protocol.get('role')!='local-RCA-development-only'
        or protocol.get('analysis_cutoff')!='maximum-operational-receipt-and-correlation-cutoff/v1'):
        raise ValueError('unsupported S5 development protocol')
    if run['scenario_id']!='S5' or run['declared_split']!='validation':
        raise ValueError('final holdout remains closed until P11; only S5 development validation is open')
    matches=[case['name'] for case in protocol['cases']
             if all(run['parameters'].get(key)==value for key,value in case.items() if key!='name')]
    if len(matches)!=1:raise ValueError('run configuration does not match one frozen S5 development case')
    return matches[0]


def post_capture_cutoff(correlation,source,evidence):
    """Predeclared forensic decision clock, separate from historical alerts."""
    clocks=[utc_time(correlation['as_of'])]
    for reference in [evidence['logs'],*evidence['traces']]:
        raw=source.read(reference)
        if not isinstance(raw.get('collected_at'),str):
            raise ValueError('S5 development evidence requires observed receipt clocks')
        clocks.append(utc_time(raw['collected_at']))
    for reference in evidence['infrastructure']:
        clocks.append(utc_time(source.read(reference)['available_at']))
    return max(clocks)


def grouping_report(correlation,roots,*,consumer='checkout-service'):
    validate_payload('correlation-result.schema.json',correlation)
    if len(roots)!=2 or len(set(roots))!=2:raise ValueError('multi-fault evaluation requires two explicit roots')
    groups=correlation['groups'];root_groups={root:[] for root in sorted(roots)}
    for group in groups:
        for root in roots:
            if root in group['affected_services']:root_groups[root].append(group['group_id'])
    merged=[g['group_id'] for g in groups if set(roots)<=set(g['affected_services'])]
    shared=[g['group_id'] for g in groups if consumer in g['affected_services']
            and g['grouping_reason']=='ambiguous-shared-consumer' and set(roots)<=set(g['structural_candidates'])]
    missing=[root for root,identifiers in root_groups.items() if not identifiers]
    return {'expected_roots':sorted(roots),'root_group_ids':root_groups,'missed_roots':missing,
        'false_merge_group_ids':merged,'ambiguous_consumer_group_ids':shared,
        'passed':not missing and not merged and bool(shared)}


def direct_support_by_root(report,archive,roots):
    support={root:set() for root in roots}
    for row in report['results']:
        result=row['rca'];facts={f['id']:f for f in result['evidence']}
        group=next(g for g in result['correlation']['groups'] if g['group_id']==row['group_id'])
        for candidate in result['candidates']:
            root=candidate['service']
            if root not in support or root not in group['affected_services']:continue
            card=candidate['components']['dependency_trace_relevance']
            if card['method'] not in ('native-direct-span-duration-share/v1','native-span-terminal-error/v1'):continue
            for identifier in card['evidence_refs']:
                fact=facts[identifier]
                if fact['type']=='trace' and archive.read(fact['source_ref']).get('request_entry_service')==root:
                    support[root].add(fact['source_ref'])
    return {root:sorted(references) for root,references in sorted(support.items())}
