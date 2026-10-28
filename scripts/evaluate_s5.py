"""Evaluate a clean local S5 DEVELOPMENT capture; P11 holdout stays closed."""
import argparse
import hashlib
import json
from pathlib import Path

import yaml

from scripts.build_dataset import load_run
from sentinelops.experiments.multifault import direct_support_by_root,grouping_report,post_capture_cutoff,selected_development_case,warmup_report
from sentinelops.incidents.health_replay import replay_health_manifest
from sentinelops.incidents.correlation_replay import replay_correlation
from sentinelops.incidents.rca_replay import replay_rca
from sentinelops.telemetry.core import stable_id,timestamp
from sentinelops.telemetry.store import ArtifactStore


def evaluate(run_path,output,protocol_path):
    protocol=json.loads(protocol_path.read_text())
    pointer=json.loads((run_path/'run.json').read_text())
    case=selected_development_case(pointer,protocol)  # Refuse heldout before reading its source/labels.
    if pointer['parameters'].get('s5_protocol_sha256')!=hashlib.sha256(protocol_path.read_bytes()).hexdigest():
        raise ValueError('S5 protocol changed since the pre-capture snapshot')
    item=load_run(run_path);run=item['run']
    if run!=pointer:raise ValueError('run pointer changed during S5 admission')
    policy=json.loads(Path('configs/rca_policy.json').read_text())
    if stable_id(policy)!=protocol['rca_policy_sha256']:raise ValueError('RCA policy differs from frozen S5 development protocol')
    output.mkdir(parents=True,exist_ok=False)
    source=ArtifactStore(run_path/'serving');manifest=source.read(run['source_manifest_ref'])
    warmup=warmup_report(source.read(run['logs_ref'])['records'],run,protocol['minimum_full_warmup_request_buckets'])
    health=replay_health_manifest(source,run['source_manifest_ref'],json.loads(Path('configs/slo_health_policy.json').read_text()),output/'health',project=Path.cwd())
    correlated=replay_correlation(ArtifactStore(output/'health'),health['report_ref'],yaml.safe_load(Path('configs/topology.yaml').read_text()),output/'correlation',project=Path.cwd())
    cutoff=post_capture_cutoff(correlated['correlation'],source,manifest['evidence_refs'])
    rca=replay_rca(source,ArtifactStore(output/'health'),ArtifactStore(output/'correlation'),correlated['report_ref'],policy,output/'rca',project=Path.cwd(),as_of=cutoff)
    # Expected roots affect evaluation only after diagnostic outputs have been produced.
    roots=run['root_causes_ground_truth']
    grouping=grouping_report(correlated['correlation'],roots)
    support=direct_support_by_root(rca,ArtifactStore(output/'rca'),roots)
    no_authority=all(row['rca']['abstained'] and row['rca']['actions_allowed'] is False for row in rca['results'])
    report={'evaluation_version':'s5-development/v1','role':'local-RCA-development-only','protocol_sha256':stable_id(protocol),
        'case':case,'run_id':run['run_id'],'session_id':run['session_id'],'source_commit':run['source_commit'],'truth_ref':item['truth_ref'],
        'health_ref':health['report_ref'],'correlation_ref':correlated['report_ref'],'rca_ref':rca['report_ref'],
        'analysis_cutoff_strategy':protocol['analysis_cutoff'],'as_of':timestamp(cutoff),'grouping':grouping,
        'direct_evidence_refs_by_root':support,'server_log_coverage':run['parameters']['server_log_coverage'],'warmup':warmup,
        'status':'PASS' if grouping['passed'] and all(support.values()) and no_authority and all(v['passed'] for v in warmup.values()) else 'FAIL',
        'models_available':False,'actions_allowed':False,'historical_emissions_verified':False,
        'full_G4_accepted':False,'heldout_opened':False}
    (output/'evaluation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if report['status']!='PASS':raise ValueError('S5 development acceptance failed; evidence retained')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--protocol',type=Path,default=Path('configs/s5_development_protocol.json'))
    args=parser.parse_args()
    evaluate(args.run,args.output,args.protocol)
