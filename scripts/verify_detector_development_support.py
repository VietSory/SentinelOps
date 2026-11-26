"""Reproduce Alibaba detector fit and measure known/novel development support."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import sentinelops

from sentinelops.experiments.local_corpus import clean_source
from sentinelops.ml.model_benchmark_detector import fit_detectors, predict_detectors
from sentinelops.ml.model_benchmark_detector_fit import (
    FIT_PATH, _calibration_ledger, _cohorts, _protocol_cohort_constraints, _verified_aggregates,
    verify_protocol_detector_fit,
)
from sentinelops.ml.model_benchmark_protocol import _ref, audit_protocol, publish_freeze
from sentinelops.telemetry.core import stable_id
from scripts.verify_ltfp_acceptance import _test_results


CHECKS = {
    'B01': ['tests/test_model_benchmark_detector.py::test_b01_matches_recorded_adsketch_author_fixture'],
    'B02': ['tests/test_model_benchmark_detector.py::test_b02_fits_only_train_segments_and_reference_scaler'],
    'B03': ['tests/test_model_benchmark_detector.py::test_b03_predictions_are_prefix_causal_and_ignore_test_labels'],
    'B04': ['tests/test_model_benchmark_detector_evaluation.py::test_b04_duplicate_alert_and_5xx_only_event_do_not_inflate_latency_tp'],
    'B05': ['tests/test_model_benchmark_detector.py::test_b05_unavailable_reasons_and_alerts_share_model_timeline'],
    'B06': ['tests/test_model_benchmark_detector.py::test_b06_separates_known_and_novel_runs_against_one_frozen_library'],
}


def summarize(predictions, anchors):
    """Count only registered calibration anchors; unavailable anchors remain visible."""
    counts, services, examples, seen = Counter(), defaultdict(set), {}, set()
    for prediction in predictions:
        if prediction.model_id != 'adsketch':
            continue
        key = (prediction.run_id, prediction.service, prediction.timestamp)
        if key not in anchors:
            continue
        if key in seen:
            raise ValueError('duplicate calibration prediction')
        seen.add(key)
        kind = prediction.pattern_kind if prediction.score is not None else 'unavailable'
        if kind not in ('normal', 'known', 'novel', 'unavailable'):
            raise ValueError('invalid detector pattern classification')
        label = anchors[key]
        counts[(kind, 'unknown' if label is None else str(label))] += 1
        services[kind].add(prediction.service)
        if kind in ('known', 'novel') and label == 1:
            examples.setdefault(kind, {'run_id': prediction.run_id, 'service': prediction.service,
                                       'timestamp': prediction.timestamp, 'pattern_id': prediction.pattern_id})
    counts[('unavailable', 'unknown')] += len(anchors) - len(seen)
    supported = (set(examples) == {'known', 'novel'}
                 and examples['known']['run_id'] != examples['novel']['run_id'])
    return {'status': 'passed' if supported else 'blocked', 'anchors': len(anchors),
            'counts': {f'{kind}:{label}': count for (kind, label), count in sorted(counts.items()) if count},
            'services': {kind: len(values) for kind, values in sorted(services.items())},
            'examples': examples}


def run(root, protocol_path, fit_root, fit_sha256, output):
    root, protocol_path, fit_root, output = (Path(path).resolve() for path in (root, protocol_path, fit_root, output))
    if not all(path.is_relative_to(root) for path in (protocol_path, fit_root, output)):
        raise ValueError('development artifacts must remain inside the benchmark root')
    project = Path(__file__).resolve().parents[1]
    if not Path(sentinelops.__file__).samefile(project / 'src/sentinelops/__init__.py'):
        raise ValueError('support verifier must import its own checkout')
    commit = clean_source(project)
    protocol = json.loads(protocol_path.read_bytes())
    audit = audit_protocol(protocol, root)
    if protocol['code']['commit'] != commit:
        raise ValueError('support verifier requires the protocol source commit')
    with tempfile.TemporaryDirectory(prefix='detector-support-tests-') as temporary:
        junit = Path(temporary) / 'pytest.xml'
        targets = [target for group in CHECKS.values() for target in group]
        process = subprocess.run([sys.executable, '-m', 'pytest', '-q', '--disable-warnings',
                                  '--junitxml=' + str(junit), *targets], cwd=project, capture_output=True, text=True)
        if process.returncode:
            raise ValueError('detector fixture checks failed: ' + (process.stdout + process.stderr)[-2000:])
        fixture_cases = _test_results(junit.read_bytes(), CHECKS)
    split_ref, source_ref = protocol['data']['splits'], protocol['code']['source_manifest']
    split_bytes, source_bytes = _ref(root, split_ref), _ref(root, source_ref)
    verified = verify_protocol_detector_fit(fit_root, expected_fit_sha256=fit_sha256,
        protocol_splits=split_bytes, protocol_splits_sha256=split_ref['sha256'],
        protocol_source_manifest=source_bytes)
    if verified.status != 'ready' or verified.provenance_sha256 != protocol['data']['manifest']['sha256']:
        raise ValueError('ready detector fit on the protocol data is required')
    checkpoint = json.loads((fit_root / FIT_PATH).read_bytes())
    provenance = root / protocol['data']['manifest']['path']
    _ref(root, protocol['data']['manifest'])
    source, minutes = _verified_aggregates(root / 'minutes', provenance, verified.provenance_sha256)
    if any(checkpoint['identity']['source'].get(key) != value for key, value in source.items()):
        raise ValueError('detector checkpoint source differs from verified aggregates')
    records = json.loads(split_bytes)['records']
    rows, anchors, cohorts = _cohorts(minutes, source, verified.model.config.window_length,
                                      constraints=_protocol_cohort_constraints(records))
    if cohorts != checkpoint['identity']['cohorts']:
        raise ValueError('detector fit cohorts do not reproduce')
    repeated = fit_detectors(rows['reference'], verified.model.config, discovery_rows=rows['discovery'])
    if repeated.digest != verified.model.digest:
        raise ValueError('detector model does not reproduce from the admitted training split')
    if _calibration_ledger(repeated, rows['calibration'], anchors, include_sources=True) != checkpoint['calibration_rows']:
        raise ValueError('detector calibration does not reproduce from the admitted anchors')
    support = dict(repeated.support)
    if not support['normal_segments'] or not support['anomalous_segments']:
        raise ValueError('normal and anomalous training support is required')
    b06 = summarize(predict_detectors(repeated, rows['calibration']), anchors)
    if clean_source(project) != commit:
        raise ValueError('support verifier source changed during replay')
    result = {'schema_version': 'detector-real-support/development-v1', 'scope': 'alibaba-proxy-development',
              'protocol_sha256': audit['protocol_sha256'], 'source_commit': commit,
              'fit_sha256': fit_sha256, 'model_sha256': repeated.digest,
              'split_sha256': split_ref['sha256'], 'source_sha256': verified.provenance_sha256,
              'fixtures': {'status': 'passed', 'checks': CHECKS, 'test_cases': fixture_cases},
              'cohorts_sha256': stable_id(cohorts), 'b02': {'status': 'passed', 'support': support},
              'b06': b06, 'holdout_opened': False, 'activation_allowed': False,
              'limitations': ['Labels are Alibaba relative-latency proxies, not local latency outcomes.',
                              'Calibration support does not measure holdout performance.']}
    content = (json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    publish_freeze(output, content, conflict='different detector support receipt already exists')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--fit-root', type=Path, required=True)
    parser.add_argument('--fit-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.protocol, args.fit_root, args.fit_sha256, args.output), sort_keys=True))


if __name__ == '__main__':
    main()
