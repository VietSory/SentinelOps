"""Deployment-lock software fixtures grant no model/remediation approval."""
import subprocess

import pytest

from scripts import lane_b_release as release
from sentinelops.ml.registry import ModelRegistry
from sentinelops.telemetry.core import stable_id
from sentinelops.telemetry.store import ArtifactStore
from test_lane_b import rendered_fixture


def source_fixture(tmp_path):
    config = rendered_fixture()
    source = tmp_path/'init.sql'; source.write_text('SELECT 1;\n')
    config['services']['postgres']['volumes'] = [{'type':'bind','source':str(source),'target':'/init.sql','read_only':True}]
    record = {'lock_version':'lane-b-deployment/v1','scope':'local-compose-images-and-bind-inputs',
        'compose_project':'sentinelops-b','observed_at':'2026-01-01T00:00:00Z','observer_commit':'a'*40,
        'observer_code_sha256':'b'*64,'config_sha256':stable_id(config),
        'bind_file_sha256':release.bind_hashes(config,tmp_path),
        'images':{name:{'image_id':'sha256:'+f'{i:064x}','container_id':f'{i:064x}'} for i,name in enumerate(config['services'],1)},
        'host_processes_pinned':False,'model_activation_allowed':False,'remediation_approval':False}
    root = tmp_path/'runtime'
    ref = ArtifactStore(root/'releases').write('manifests',record)
    return config,record,root,ref


def test_locked_override_has_exact_local_images_no_build_or_pull_and_is_immutable(tmp_path,monkeypatch):
    config, record, root, ref = source_fixture(tmp_path)
    monkeypatch.setattr(release.subprocess,'check_output',lambda command,**kwargs:command[-1]+'\n')
    path = release.materialize(config,ref,project=tmp_path,root=root)
    content = path.read_text()
    assert content.count('pull_policy: never') == 8 and content.count('build: !reset null') == 8
    assert all(image['image_id'] in content for image in record['images'].values())
    assert release.materialize(config,ref,project=tmp_path,root=root) == path
    path.write_text('changed override')
    with pytest.raises(ValueError,match='immutable deployment override'):
        release.materialize(config,ref,project=tmp_path,root=root)


@pytest.mark.parametrize('change',['config','bind'])
def test_changed_configuration_or_mounted_bytes_rejected_before_image_or_deployment_io(tmp_path,monkeypatch,change):
    config, _, root, ref = source_fixture(tmp_path)
    if change == 'config': config['services']['postgres']['environment'] = {'unexpected':'change'}
    else: (tmp_path/'init.sql').write_text('SELECT 2;\n')
    monkeypatch.setattr(release.subprocess,'check_output',lambda *a,**k:pytest.fail('changed inputs reached Docker'))
    with pytest.raises(ValueError,match='configuration/bind bytes differ'):
        release.materialize(config,ref,project=tmp_path,root=root)
    assert not (root/'release-overrides').exists()


def test_absent_image_never_falls_back_to_a_mutable_tag_or_partial_override(tmp_path,monkeypatch):
    config, _, root, ref = source_fixture(tmp_path)
    def missing(command,**kwargs): raise subprocess.CalledProcessError(1,command)
    monkeypatch.setattr(release.subprocess,'check_output',missing)
    with pytest.raises(subprocess.CalledProcessError): release.materialize(config,ref,project=tmp_path,root=root)
    assert not (root/'release-overrides').exists()


def test_deployment_lock_cannot_be_model_release_or_remediation_approval(tmp_path,monkeypatch):
    config, record, root, ref = source_fixture(tmp_path)
    store = ArtifactStore(root/'releases')
    with pytest.raises(ValueError,match='unsupported model release'):
        ModelRegistry.load(store,ref)
    record['remediation_approval'] = True
    forged = store.write('manifests',record)
    monkeypatch.setattr(release.subprocess,'check_output',lambda *a,**k:pytest.fail('forged approval reached Docker'))
    with pytest.raises(ValueError): release.materialize(config,forged,project=tmp_path,root=root)


def test_nonowned_or_writable_bind_inputs_do_not_form_a_release(tmp_path):
    config, _, _, _ = source_fixture(tmp_path)
    mount = config['services']['postgres']['volumes'][0]
    mount['read_only'] = False
    with pytest.raises(ValueError,match='owned read-only bind files'): release.bind_hashes(config,tmp_path)
    mount.update(read_only=True,source='/etc/passwd')
    with pytest.raises(ValueError,match='owned read-only bind files'): release.bind_hashes(config,tmp_path)


def test_dirty_observer_cannot_claim_an_immutable_source_identity(tmp_path,monkeypatch):
    config, _, root, _ = source_fixture(tmp_path)
    monkeypatch.setattr(release.lane_b,'require_owned_containers',lambda:None)
    monkeypatch.setattr(release.subprocess,'check_output',lambda *a,**k:b' M observer.py\n')
    with pytest.raises(ValueError,match='commit the release observer'):
        release.snapshot(config,project=tmp_path,root=root)
