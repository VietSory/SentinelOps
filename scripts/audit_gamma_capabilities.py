"""Recompute source-backed capability matrix without opening heldout cases."""
import argparse
import json
from pathlib import Path

from sentinelops.public_data.gamma_capabilities import audit_capabilities


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--grouped-report', type=Path, default=Path('docs/reports/evidence/2026-10-26-gamma-grouped-dataset.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit_capabilities(args.manifest, json.loads(args.grouped_report.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({k: result[k] for k in ('G2', 'G3', 'healthy_certificates_generated', 'precursor_labels_generated', 'fitted_models')}))


if __name__ == '__main__':
    main()
