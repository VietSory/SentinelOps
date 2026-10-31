import pytest
from scripts.ci_impact import classify, changed_paths


def test_ui_and_docs_do_not_require_native_measurements_but_core_changes_do():
    assert classify(['docs/TEAM_HANDOFF.md']) == {'native':False,'public_data':False,'ui':False}
    assert classify(['src/sentinelops/ui/assets/styles.css','tests/ui/workspace.test.mjs']) == {'native':False,'public_data':False,'ui':True}
    for path in ('src/sentinelops/api.py','src/sentinelops/ml/models.py','src/sentinelops/workers/incident.py',
                 'infra/control/004-incident-consumer.sql','contracts/incident-envelope.schema.json',
                 'configs/slo_health_policy.json','.github/workflows/ci.yml','unknown/new-module.py'):
        assert all(classify([path]).values())


def test_release_manual_run_requires_all_checks_and_unknown_events_fail_closed():
    assert all(classify([],force_full=True).values())
    assert changed_paths('workflow_dispatch',{}) == ([],True)
    with pytest.raises(ValueError): changed_paths('unrecognized',{})
    with pytest.raises(ValueError): changed_paths('push',{'before':'HEAD~1','after':'bad'})


def test_initial_push_cannot_be_misclassified_as_no_change():
    assert changed_paths('push',{'before':'0'*40,'after':'a'*40}) == ([],True)


def test_renaming_core_into_ui_cannot_escape_native_checks(tmp_path,monkeypatch):
    import subprocess
    def git(*args):
        return subprocess.check_output(['git',*args],cwd=tmp_path,text=True).strip()
    git('init','-q')
    old = tmp_path/'src/sentinelops/workers/worker.py'; old.parent.mkdir(parents=True)
    old.write_text('source-code\n')
    git('add','.')
    git('-c','user.name=Test Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture baseline')
    base = git('rev-parse','HEAD')
    new = tmp_path/'src/sentinelops/ui/worker.py'; new.parent.mkdir(parents=True)
    old.rename(new); git('add','-A')
    git('-c','user.name=Test Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture rename')
    head = git('rev-parse','HEAD')
    monkeypatch.chdir(tmp_path)
    paths, forced = changed_paths('push',{'before':base,'after':head})
    assert 'src/sentinelops/workers/worker.py' in paths and 'src/sentinelops/ui/worker.py' in paths
    assert not forced and all(classify(paths).values())


def test_workflow_summary_requires_requested_jobs_and_allows_only_out_of_scope_skips():
    import json,os,subprocess,sys
    from pathlib import Path
    import yaml
    workflow=yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/ci.yml').read_text(),Loader=yaml.BaseLoader)
    summary=workflow['jobs']['ci-summary']
    assert summary['if']=='always()' and 'workflow_dispatch' in workflow['on']
    code='\n'.join(summary['steps'][0]['run'].splitlines()[1:-1])
    names=['changes','foundation','public-data','ui','incident-storage','observability','multi-fault-development','health-worker-integration','incident-worker-s5']
    def execute(needs):
        return subprocess.run([sys.executable,'-c',code],env={**os.environ,'NEEDS_JSON':json.dumps(needs)},capture_output=True).returncode
    needs={name:{'result':'success'} for name in names}
    needs['changes']['outputs']={'native':'true','public_data':'true','ui':'true'}
    assert execute(needs)==0
    needs['incident-worker-s5']['result']='failure'; assert execute(needs)!=0
    needs['incident-worker-s5']['result']='skipped'; assert execute(needs)!=0
    needs['changes']['outputs']={'native':'false','public_data':'true','ui':'true'}
    assert execute(needs)==0
    needs['changes']['result']='failure'; assert execute(needs)!=0
