"""Bind measured Alibaba development support to the Lane A protocol draft."""
import argparse
from collections import Counter
import json
from pathlib import Path
import tempfile

from scripts.calibrate_ltfp_development import verify as verify_calibration
from sentinelops.ml.model_benchmark_protocol import _json_ref, audit_protocol
from sentinelops.public_data.alibaba import file_sha256


PILOTS = {
    'cohort-20-23.json': '9e24275c95ad7994ee618bfca66a23163d5ea76397974b9cc282f97af62c173a',
    'cohort-48-51.json': '9e9e8153e8ddcffcc9bc91d1cac45ec467a8c8aa48f9998ef6833ff07cd2139f',
}
COHORT = 'cohort-causal-time-purged-v5.json'
COHORT_SHA = '07f4ae2395beaa484dd070653ee60d6e2d3a8cdb3a500951784b6c2f77e53ddd'


def _require_support(cohort, fitted, scored):
    if ({row['role'] for row in cohort} != {'train', 'calibration'} or
            any(min(row['positive'], row['negative_with_neighbor']) <= 0 for row in cohort)):
        raise ValueError('both development partitions require labelled windows and usable graph edges')
    if not fitted[0] or not fitted[1]:
        raise ValueError('fit requires both label classes')
    if not scored or any(counts[0] < 100 or not counts[1] for counts in scored.values()):
        raise ValueError('each calibration model requires a positive and at least 100 scored negatives')


def run(root):
    root = Path(root).resolve()
    preflight = root / 'preflight'
    protocol = json.loads((preflight / 'protocol-draft-v3/protocol.json').read_bytes())
    previous = audit_protocol(protocol, root)
    if previous['lanes']['a']['reasons'] != [
            'A07: evidence missing', 'A08: evidence missing', 'real train/calibration support unavailable']:
        raise ValueError('expected the verified Lane A draft with A08 and support outstanding')
    verify_calibration(root)
    fit = json.loads((preflight / 'development-k16-result-v5.json').read_bytes())
    ledger = preflight / 'development-k16-calibration-v5/predictions.json'
    calibration = json.loads(ledger.read_bytes())
    splits = _json_ref(root, protocol['data']['splits'])['records']
    for role, rows in (('train', fit['selected_records']), ('calibration', calibration)):
        expected = {(row['service'], row['minute']) for row in rows}
        declared = {(row['service'], row['anchor_minute']) for row in splits if row['role'] == role}
        if len(expected) != len(rows) or expected != declared:
            raise ValueError(f'{role} cohort differs from protocol split')

    pilot_refs = {name: {'path': f'preflight/{name}', 'sha256': sha} for name, sha in PILOTS.items()}
    cohort_ref = {'path': f'preflight/{COHORT}', 'sha256': COHORT_SHA}
    cohort = _json_ref(root, cohort_ref)
    if (cohort['fit_report_sha256'] != protocol['data']['manifest']['sha256'] or
            cohort['graph_archives'] != fit['protocol']['graph_archives']):
        raise ValueError('development cohort source differs from protocol or fit')
    pilots = {name: _json_ref(root, ref)['results'] for name, ref in pilot_refs.items()}
    for rows in pilots.values():
        if ({(row['split'], tuple(row['period'])) for row in rows} != {
                (role, period) for role in ('train', 'calibration')
                for period in ((162, 191), (324, 353))} or
                any(row['negative_with_neighbor'] < 100 for row in rows)):
            raise ValueError('pilot coverage or negative graph support missing')
    fitted = Counter(row['label'] for row in fit['selected_records'])
    models = ('slope', 'xgboost', 'ltfp_self_only', 'ltfp_graph')
    scored = {model: Counter(row['risk_label'] for row in calibration if row['scores'][model] is not None)
              for model in models}
    _require_support(cohort['results'], fitted, scored)
    graph = [row for row in calibration if row['scores']['ltfp_graph'] is not None]
    connected = Counter(row['risk_label'] for row in graph if row['available_neighbors'] > 0)
    support = _json_ref(root, protocol['data']['support'])
    support['lanes']['a'] = {
        'status': 'ready', 'scope': 'real', 'dataset_scope': 'alibaba-proxy-development',
        'cohort_receipt': cohort_ref, 'pilot_receipts': pilot_refs,
        'calibration_ledger_sha256': file_sha256(ledger), 'admitted_cohorts': cohort['results'],
        'pilot_cohorts': pilots, 'positive_group_unit': 'service',
        'fit': {'positive': fitted[1], 'negative': fitted[0]},
        'calibration_scored': {model: {'positive': counts[1], 'negative': counts[0], 'unknown': counts[None]}
                               for model, counts in scored.items()},
        'calibration_graph': {'positive_with_neighbor': connected[1], 'negative_with_neighbor': connected[0],
                              'unknown_with_neighbor': connected[None],
                              'scored_without_available_neighbor': len(graph) - sum(connected.values())},
        'limitations': ['No positive calibration anchor has an available same-split neighbor.',
                        'Post-pilot development selections and simulated metric availability.'],
    }
    output = preflight / 'protocol-draft-v4'
    if output.exists():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(prefix='.support-', dir=preflight) as temporary:
        stage = Path(temporary)

        def write(name, value):
            path = stage / name
            path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')
            return {'path': (output / name).relative_to(root).as_posix(), 'sha256': file_sha256(path)}

        protocol['data']['support'] = write('support.json', support)
        a08 = dict(test_id='A08', status='passed', scope='real')
        evidence = write('A08.json', {**a08, 'source_manifest_sha256': protocol['code']['source_manifest']['sha256']})
        protocol['checks'].append({**a08, 'evidence': evidence})
        write('protocol.json', protocol)
        stage.rename(output)
    result = audit_protocol(protocol, root)
    if result['lanes']['a']['reasons'] != ['A07: evidence missing']:
        raise ValueError('development support receipt did not retain the expected A07 gate')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root), sort_keys=True))


if __name__ == '__main__':
    main()
