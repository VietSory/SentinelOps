"""Protocol and orchestration fixtures; these do not admit real data or pass G4."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from sentinelops.experiments.local_corpus import bind
from sentinelops.incidents import local_evaluation as evaluation
from sentinelops.ml.readiness import DatasetNotReady
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore

ROOT = Path(__file__).resolve().parents[1]


def at(minute):
    return f'2026-11-01T00:{minute:02d}:00Z'


def diagnostic(rank=None, *, abstained=True, onset=5, identifier='first'):
    rank = [] if rank is None else rank
    group = {'group_id': identifier, 'first_onset_at': at(onset), 'structural_candidates': rank}
    candidates = [{'service': service, 'score': 1 / position, 'components': {
        'anomaly_strength': {'value': None}, 'temporal_precedence': {'value': None}}}
        for position, service in enumerate(rank, start=1)]
    return {'report_ref': 'artifact://manifests/' + 'd' * 64 + '.json', 'results': [{
        'group_id': identifier, 'rca': {'correlation': {'groups': [group]}, 'ranking': rank, 'candidates': candidates,
        'abstained': abstained, 'abstention_reasons': ['insufficient-coverage'] if abstained else [],
        'actions_allowed': False}}]}


def trial(kind='payment-gradual'):
    expected = evaluation.ROOTS[kind]
    return {'kind': kind, 'run_id': 'run-test', 'group_id': 'dev-01',
        'source_result_ref': 'source', 'oracle_ref': 'oracle',
        'oracle': {'services': {s: {'confirmed_at': at(8) if s == expected else None}
            for s in ('checkout-service', 'payment-service', 'inventory-service')}}}


def test_first_group_selection_cannot_pick_later_correct_root_and_does_not_mutate_diagnostics():
    first = diagnostic(['inventory-service'], abstained=False, onset=4, identifier='a')
    later = diagnostic(['payment-service'], abstained=False, onset=6, identifier='b')
    report = {'report_ref': first['report_ref'], 'results': later['results'] + first['results']}
    before = deepcopy(report)
    result = evaluation.trial_result(trial(), {'root_cause_ground_truth': 'payment-service'}, report)
    assert result['selected_group_id'] == 'a'
    assert not result['diagnostic_top1_hit'] and not result['qualified_top1_hit']
    assert report == before
    # Equal-onset tie-breaking is also independent of result order/root identity.
    later['results'][0]['rca']['correlation']['groups'][0]['first_onset_at'] = at(4)
    report['results'] = later['results'] + first['results']
    assert evaluation.trial_result(trial(), {'root_cause_ground_truth': 'payment-service'}, report)['selected_group_id'] == 'a'


def test_missing_group_abstention_wrong_root_and_no_confirmed_slo_keep_trial_denominators():
    run = {'root_cause_ground_truth': 'payment-service'}
    missing = evaluation.trial_result(trial(), run, {'report_ref': 'none', 'results': []})
    abstained = evaluation.trial_result(trial(), run, diagnostic(['payment-service']))
    wrong = evaluation.trial_result(trial(), run, diagnostic(['inventory-service'], abstained=False))
    source = trial(); source['oracle']['services']['payment-service']['confirmed_at'] = None
    correct = evaluation.trial_result(source, run, diagnostic(['payment-service'], abstained=False))
    healthy = evaluation.trial_result(trial('healthy'), {'root_cause_ground_truth': None}, diagnostic(['payment-service']))
    result = evaluation.summarize_trials([missing, abstained, wrong, correct, healthy])
    assert result['fault_trials'] == 4 and result['healthy_trials'] == 1
    assert result['fault_trials_without_incident_group'] == 1
    assert result['fault_trials_with_abstention'] == 1
    assert result['fault_trials_without_independent_confirmed_slo'] == 1
    assert result['healthy_trials_with_incident_group'] == 1
    assert result['diagnostic_top1_per_fault_trial'] == .5
    assert result['qualified_top1_per_fault_trial'] == .25
    assert result['conditional_qualified_top1'] == .5
    only_abstained = evaluation.summarize_trials([abstained])
    assert only_abstained['conditional_qualified_top1'] is None
    assert evaluation.summarize_trials([])['qualified_top1_per_fault_trial'] is None


def test_ablation_metrics_count_missing_groups_and_ties_without_breaking_abstention():
    payment, inventory = 'payment-service', 'inventory-service'
    run = {'root_cause_ground_truth': payment}
    source = diagnostic([payment, inventory])
    candidates = source['results'][0]['rca']['candidates']
    for candidate in candidates:
        candidate['components']['anomaly_strength']['value'] = .5
    candidates[0]['components']['temporal_precedence']['value'] = 1
    abstained = evaluation.trial_result(trial(), run, source)
    assert abstained['comparison_ranks']['anomaly-only'] == {payment: 2, inventory: 2}
    assert abstained['comparison_ranks']['topology-time-only'] == {payment: 2, inventory: 2}
    assert abstained['comparison_ranks']['full-qualified'] == {}
    candidates[1]['components']['temporal_precedence']['value'] = 0
    separated = evaluation.trial_result(trial(), run, source)
    assert separated['comparison_ranks']['topology-time-only'] == {payment: 1, inventory: 2}

    missing = evaluation.trial_result(trial(), run, {'report_ref': 'none', 'results': []})
    metrics = evaluation.summarize_comparisons([abstained, separated, missing])
    assert metrics['full-qualified']['mrr'] == 0
    assert metrics['full-diagnostic']['mrr'] == pytest.approx(2 / 3)
    assert metrics['anomaly-only'] == {'fault_trials': 3, 'ranked_fault_trials': 2,
                                      'top1': 0, 'top3': pytest.approx(2 / 3), 'mrr': pytest.approx(1 / 3)}
    assert metrics['topology-time-only']['mrr'] == .5


def test_truth_mismatch_and_action_authority_are_rejected():
    with pytest.raises(ValueError, match='truth differs'):
        evaluation.trial_result(trial(), {'root_cause_ground_truth': 'inventory-service'}, diagnostic())
    changed = diagnostic(); changed['results'][0]['rca']['actions_allowed'] = True
    with pytest.raises(ValueError, match='action authority'):
        evaluation.trial_result(trial(), {'root_cause_ground_truth': 'payment-service'}, changed)


@pytest.mark.parametrize('change', [{'minimum_whole_groups': True}, {'final_access_allowed': True},
    {'group_selection': 'best-correct-root'}, {'extra': 'unknown'}])
def test_protocol_is_frozen(tmp_path, change):
    (tmp_path/'configs').mkdir()
    (tmp_path/'configs/local_rca_evaluation.json').write_text(json.dumps({**evaluation.PROTOCOL, **change}))
    with pytest.raises(ValueError, match='protocol is frozen'):
        evaluation.load_protocol(tmp_path)


def test_real_blocked_source_refuses_before_output_and_diagnostics(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path/'source')
    ref = store.write('manifests', {'dataset_version': 'local-http-admission/v1', 'training_ready': False})
    def forbidden(*args, **kwargs):
        raise AssertionError('blocked source reached diagnostics or project access')
    monkeypatch.setattr(evaluation, 'clean_source', forbidden)
    monkeypatch.setattr(evaluation, 'replay_health_manifest', forbidden)
    with pytest.raises(DatasetNotReady, match='G2 is blocked'):
        evaluation.evaluate_local_rca(store, ref, tmp_path/'output', project=tmp_path/'missing')
    assert not (tmp_path/'output').exists()


@pytest.fixture
def orchestration(tmp_path, monkeypatch):
    """Explicitly mock source admission and diagnostics, preserve real store/freeze/cutoff checks."""
    project = tmp_path/'project'; (project/'configs').mkdir(parents=True)
    for name in ('local_rca_evaluation.json', 'rca_policy.json', 'slo_health_policy.json', 'topology.yaml'):
        (project/'configs'/name).write_bytes((ROOT/'configs'/name).read_bytes())
    store = ArtifactStore(tmp_path/'source')
    report = {'source_profile': evaluation.PROTOCOL['source_profile']}
    ref = bind(store, store.root/'dataset-freeze.json', report)
    cases, paths = [], []
    for ordinal in range(1, 6):
        for kind in evaluation.KINDS:
            group = f'dev-{ordinal:02d}'; identifier = 'run-' + stable_id([group, kind])[:32]
            relative = f'runs/{group}-{kind}/captures/{identifier}'
            path = store.root/relative; path.mkdir(parents=True)
            source = ArtifactStore(path/'serving')
            logs_ref = source.write('raw', {'collected_at': at(12)})
            manifest_ref = source.write('manifests', {'evidence_refs': {'logs': logs_ref, 'traces': [], 'infrastructure': []}})
            run = {'root_cause_ground_truth': evaluation.ROOTS[kind], 'source_manifest_ref': manifest_ref,
                'run_id': identifier, 'session_id': group, 'declared_split': 'validation',
                'source_profile': evaluation.PROTOCOL['source_profile']}
            (path/'run.json').write_text(json.dumps(run))
            case = {**trial(kind), 'group_id': group, 'run_id': identifier, 'started_at': at(0),
                'role': 'development-validation', 'source_result_ref': store.write('manifests', {
                    'run_directory': relative, 'run_sha256': stable_id(run)})}
            cases.append(case); paths.append(path)
    monkeypatch.setattr(evaluation, 'admitted_local_evaluation_rows', lambda *_: ([], cases))
    monkeypatch.setattr(evaluation, 'clean_source', lambda *_: 'c'*40)
    calls = []
    def health(source, manifest_ref, policy, output, **kwargs):
        calls.append(('health', manifest_ref))
        assert set(source.read(manifest_ref)) == {'evidence_refs'}
        return {'report_ref': 'health'}
    def correlation(source, health_ref, topology, output, **kwargs):
        return {'report_ref': 'correlation', 'correlation': {'as_of': at(10)}}
    def rca(source, health, correlation, correlation_ref, policy, output, **kwargs):
        calls.append(('rca', kwargs['as_of']))
        return {'report_ref': 'diagnostic', 'results': []}
    monkeypatch.setattr(evaluation, 'replay_health_manifest', health)
    monkeypatch.setattr(evaluation, 'replay_correlation', correlation)
    monkeypatch.setattr(evaluation, 'replay_rca', rca)
    return store, ref, project, cases, paths, calls


def test_orchestration_covers_all_trials_without_reading_foreign_roles(orchestration, tmp_path):
    store, ref, project, cases, _, calls = orchestration
    cases += [{'role': 'train', 'source_result_ref': 'must-not-open'},
              {'role': 'sealed-final', 'source_result_ref': 'must-not-open'}]
    result = evaluation.evaluate_local_rca(store, ref, tmp_path/'output', project=project)
    assert result['counts']['trials'] == 15 and result['counts']['whole_groups'] == 5
    assert result['counts']['fault_trials'] == result['counts']['fault_trials_without_incident_group'] == 10
    assert result['counts']['qualified_top1_per_fault_trial'] == 0
    assert len(calls) == 30 and all(c[1].isoformat().endswith('00:12:00+00:00') for c in calls if c[0] == 'rca')
    assert not result['full_G4_accepted'] and not result['holdout_opened'] and not result['actions_allowed']
    assert json.loads((tmp_path/'output/evaluation.json').read_bytes()) == result


@pytest.mark.parametrize('failure', ['missing-freeze', 'changed-freeze', 'missing-kind', 'changed-policy',
    'changed-pointer', 'symlink', 'source-output', 'existing-output'])
def test_preflight_failures_leave_no_diagnostics(orchestration, tmp_path, failure):
    store, ref, project, cases, paths, calls = orchestration
    output = tmp_path/'output'; expected = (ValueError, DatasetNotReady, FileExistsError)
    if failure == 'missing-freeze':
        (store.root/'dataset-freeze.json').unlink()
    elif failure == 'changed-freeze':
        ref = store.write('manifests', {'different': True})
    elif failure == 'missing-kind':
        cases.pop()
    elif failure == 'changed-policy':
        p = project/'configs/rca_policy.json'; value = json.loads(p.read_bytes()); value['minimum_score'] = .1
        p.write_text(json.dumps(value))
    elif failure == 'changed-pointer':
        p = paths[0]/'run.json'; value = json.loads(p.read_bytes()); value['declared_split'] = 'test'
        p.write_text(json.dumps(value))
    elif failure == 'symlink':
        original = paths[0]/'run.json'; target = tmp_path/'foreign-run.json'
        target.write_bytes(original.read_bytes()); original.unlink(); original.symlink_to(target)
    elif failure == 'source-output':
        output = store.root/'new-output'
    else:
        output.mkdir()
    with pytest.raises(expected):
        evaluation.evaluate_local_rca(store, ref, output, project=project)
    assert not calls and not (output/'evaluation.json').exists()
