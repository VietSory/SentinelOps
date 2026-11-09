import json

from scripts.summarize_g6_trials import summarize, trial


def write(tmp_path, name, outcome, scenario=None, chain_ok=True, release='r1'):
    d = tmp_path / name
    d.mkdir()
    history = [{'phase': 'prepared', 'event_hash': 'a', 'previous_hash': None},
               {'phase': outcome, 'event_hash': 'b', 'previous_hash': 'a' if chain_ok else 'x'}]
    (d / 'action-journal.json').write_text(json.dumps({'action_id': 'id', 'phase': outcome, 'history': history,
                                                       'last_event_hash': 'b'}))
    v = {'model_release_ref': release, 'action_outcome': outcome, 'action': {'action_id': 'id'}, 'h4': {}}
    if scenario:
        v['scenario'] = scenario
    (d / 'verification.json').write_text(json.dumps(v))
    return d


def test_releases_are_not_pooled_and_broken_chains_fail_the_audit(tmp_path):
    rows = [trial(write(tmp_path, 'a', 'recovered')), trial(write(tmp_path, 'b', 'recovered', release='r2')),
            trial(write(tmp_path, 'c', 'escalated', 'commerce-db-unavailable-after-rollback')),
            trial(write(tmp_path, 'd', 'escalated', 'commerce-db-unavailable-after-rollback', chain_ok=False))]
    result = summarize(rows)
    assert set(result['golden_path']) == {'r1', 'r2'} and result['golden_path']['r1']['recovered'] == 1
    assert result['failed_verify'] == {'trials': 2, 'escalated_with_complete_audit': 1,
                                       'target': '100% escalated with audit'}
