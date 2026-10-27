"""Verify a local captured evidence chain and produce analytical RCA hypotheses."""
import argparse
import json
from pathlib import Path

from sentinelops.contracts import utc_time
from sentinelops.incidents.rca_replay import replay_rca
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--telemetry-store',type=Path,required=True)
    parser.add_argument('--health-store',type=Path,required=True)
    parser.add_argument('--correlation-store',type=Path,required=True)
    parser.add_argument('--correlation-ref',required=True)
    parser.add_argument('--policy',type=Path,default=Path('configs/rca_policy.json'))
    parser.add_argument('--as-of',help='UTC analytical cutoff; does not claim an actual historical emission')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=replay_rca(ArtifactStore(args.telemetry_store),ArtifactStore(args.health_store),ArtifactStore(args.correlation_store),
        args.correlation_ref,json.loads(args.policy.read_text()),args.output,project=Path.cwd(),as_of=utc_time(args.as_of) if args.as_of else None)
    summary={key:value for key,value in report.items() if key not in ('results','source_artifact_refs')}
    summary['results']=[{'group_id':row['group_id'],'severity':row['severity'],'ranking':row['rca']['ranking'],
        'scores':{candidate['service']:candidate['score'] for candidate in row['rca']['candidates']},
        'abstention_reasons':row['rca']['abstention_reasons'],'source_counts':row['source_counts']} for row in report['results']]
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
