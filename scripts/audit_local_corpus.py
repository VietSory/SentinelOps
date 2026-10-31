"""Recompute prospective source/oracle certificates and exact G2 deficits."""
import argparse
import json
from pathlib import Path

from sentinelops.ml.local_data import audit_local_corpus, freeze_local_corpus
from sentinelops.telemetry.store import ArtifactStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--freeze', action='store_true', help='Bind only a fully recomputed ready corpus; blocks further acquisition in this epoch.')
    args = parser.parse_args()
    report, _, _ = audit_local_corpus(ArtifactStore(args.root))
    reference = ArtifactStore(args.root).write('manifests', report)
    if args.freeze:
        frozen = freeze_local_corpus(ArtifactStore(args.root), report)
        if frozen != reference:
            raise ValueError('frozen admission differs from the audited report')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump({'dataset_ref':reference,'report':report},stream,indent=2)
        stream.write('\n')
    print(json.dumps({'dataset_ref':reference,**{k:report[k] for k in ('status','registered_groups','verified_completed_runs','verified_feature_rows','counts')}}))


if __name__ == '__main__':
    main()
