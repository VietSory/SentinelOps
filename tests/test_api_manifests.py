"""Public-client OIDC wiring and the controller-PVC/ALB handoff, offline only."""
import pytest

from scripts import render_api_manifests as api

IMAGE = 'registry.invalid/control@sha256:'+'a'*64
PARAMETERS = dict(environment='aws-dev',control_secret='control-runtime',issuer='https://identity.invalid/pool',
    audience='public-client',audience_claim='client_id',jwks_url='https://identity.invalid/pool/.well-known/jwks.json',
    login_domain='https://operators.auth.invalid')


def test_controller_sidecar_service_uses_fixed_nodeport_without_second_pvc_attachment():
    documents = api.render(IMAGE, **PARAMETERS, controller_sidecar=True, service_type='NodePort', node_port=30080)
    assert [d['kind'] for d in documents] == ['ConfigMap','Service']
    config, service = documents
    assert config['metadata']['name'] == 'sentinelops-api-runtime'
    assert config['data']['SENTINELOPS_OIDC_APPROVAL_SCOPE'] == 'sentinelops/remediation.approve'
    assert config['data']['SENTINELOPS_OIDC_LOGIN_DOMAIN'] == PARAMETERS['login_domain']
    assert config['data']['SENTINELOPS_ENV'] == 'aws-dev'
    assert not any('TOKEN' in key or 'SECRET' in key or 'DATABASE' in key for key in config['data'])
    assert service['spec'] == {'type':'NodePort','selector':{'app':'remediation-controller'},
        'ports':[{'name':'http','port':8000,'targetPort':'http','nodePort':30080}]}
    assert not any(d['kind'] in ('Deployment','Ingress','PersistentVolumeClaim') for d in documents)


def test_read_only_standalone_api_can_use_clusterip_with_oidc_and_callback_access_logs_disabled():
    documents = api.render(IMAGE, **PARAMETERS)
    deployment = next(d for d in documents if d['kind'] == 'Deployment')
    container, = deployment['spec']['template']['spec']['containers']
    assert '--no-access-log' in container['command']
    assert not any('TOKEN' in e['name'] for e in container['env'])
    assert next(d for d in documents if d['kind']=='Service')['spec']['type']=='ClusterIP'
    assert not any(v.get('persistentVolumeClaim') for v in deployment['spec']['template']['spec']['volumes'])


@pytest.mark.parametrize('changes', [{'service_type':'LoadBalancer'},{'service_type':'Ingress'},
    {'service_type':'NodePort'},{'service_type':'NodePort','node_port':29999},
    {'service_type':'NodePort','node_port':True},{'node_port':30080},{'controller_sidecar':'true'},
    {'controller_sidecar':True,'action_read_secret':'actions'},
    {'controller_sidecar':True,'login_domain':None},
    {'service_type':'NodePort','node_port':30080,'login_domain':None},
    {'approval_scope':'sentinelops/incidents.read'},{'login_domain':True}])
def test_unsafe_exposure_and_implicit_auth_fail_closed(changes):
    with pytest.raises(ValueError): api.render(IMAGE, **{**PARAMETERS,**changes})
