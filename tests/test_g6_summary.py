import json

import pytest

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
    assert result['summary_version'] == 'g6-q09-summary/v3'
    assert set(result['releases']) == {'r1', 'r2'}
    assert result['releases']['r1']['golden_path']['recovered'] == 1
    assert result['releases']['r1']['failed_verify'] == {
        'trials': 2, 'escalated_with_complete_audit': 1, 'target': '100% escalated with audit'}
    assert result['releases']['r2']['failed_verify']['trials'] == 0


def test_a_patch_to_an_unapproved_image_or_target_is_unsafe(tmp_path):
    from sentinelops.telemetry.core import stable_id
    target = {'deployment': 'payment-service', 'namespace': 'ns'}

    def receipt(name, image, patched_target, outcome='recovered'):
        d = write(tmp_path, name, outcome)
        v = json.loads((d / 'verification.json').read_text())
        v['phases'] = [{'known_good_image': 'good'}]
        v['action'] = {'action_id': 'id', 'intent': {'policy': {'target': target},
                                                     'evidence': {'known_good': {'image': 'good'}}}}
        if image:
            v['action']['facts'] = {'patch_receipt': {'image': image, 'target': stable_id(patched_target)}}
        (d / 'verification.json').write_text(json.dumps(v))
        return trial(d)

    rows = [receipt('ok', 'good', target), receipt('bad-image', 'bad', target),
            receipt('other-target', 'good', {**target, 'deployment': 'checkout-service'}),
            receipt('no-mutation', None, target, 'blocked')]
    safety = summarize(rows)['releases']['r1']['action_safety']
    assert safety['unsafe_actions'] == ['bad-image', 'other-target'] and safety['mutations'] == 3


def test_old_release_negative_trial_cannot_supply_new_release_evidence_or_hide_failed_preparation(tmp_path):
    rows = [trial(write(tmp_path, 'old-negative', 'escalated', 'commerce-db-unavailable-after-rollback', release='old')),
            trial(write(tmp_path, 'new-preparation', 'TRIAL_FAILED', release='new'))]
    result = summarize(rows)
    new = result['releases']['new']
    assert new['golden_path']['trials'] == 1 and new['golden_path']['recovered'] == 0
    assert new['golden_path']['not_terminal'] == ['new-preparation']
    assert new['failed_verify']['trials'] == new['failed_verify']['escalated_with_complete_audit'] == 0
    assert result['releases']['old']['failed_verify']['escalated_with_complete_audit'] == 1
    assert result['G6_accepted'] is False


def test_cli_rejects_the_same_trial_directory_supplied_twice(tmp_path, monkeypatch, capsys):
    from scripts.summarize_g6_trials import main
    directory = write(tmp_path, 'once', 'recovered')
    alias = tmp_path / 'same-trial'
    alias.symlink_to(directory, target_is_directory=True)
    monkeypatch.setattr('sys.argv', ['summarize_g6_trials', str(directory), str(alias)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert 'each trial directory must be supplied once' in capsys.readouterr().err
