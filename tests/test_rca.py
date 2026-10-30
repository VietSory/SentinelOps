from copy import deepcopy
from datetime import timedelta
import json
import math
from pathlib import Path

import pytest

from sentinelops.contracts import validate_payload
from sentinelops.incidents.rca import COMPONENTS, evaluate_rca, temporal_precedence, unavailable
from sentinelops.telemetry.core import stable_id
from test_correlation import END, correlate, opening

POLICY = json.loads((Path(__file__).resolve().parents[1]/'configs/rca_policy.json').read_text())
AS_OF = END+timedelta(hours=1)
REF = 'fixture://rca/software-only'
SERVICES = ('checkout-service','payment-service','inventory-service','rds')
KINDS = {'anomaly_strength':'model','temporal_precedence':'timeline','dependency_trace_relevance':'trace',
         'log_support':'log','recent_change_support':'change'}
METHODS = {'anomaly_strength':'if-normalized-score/v1','temporal_precedence':'disjoint-onset-intervals/v1',
           'dependency_trace_relevance':'native-span-duration-share/v1','log_support':'business-completion-support/v1',
           'recent_change_support':'observed-deployment/v1'}


def context():
    correlation=correlate([opening('payment-service'),opening('checkout-service')])
    return correlation,correlation['groups'][0]['group_id']


def fixtures():
    facts=[];cards=[]
    for service in SERVICES:
        components={name:unavailable() for name in COMPONENTS}
        for component in COMPONENTS:
            kind=KINDS[component]
            if component=='temporal_precedence' or (service=='rds' and component in ('log_support','recent_change_support')):
                continue
            if service=='rds' and component=='anomaly_strength':kind='metric'
            identifier=service+'/'+component
            facts.append({'id':identifier,'type':kind,'service':service,'observed_at':'2026-10-26T00:03:00Z',
                          'fact':'Synthetic component evidence for software tests only','source_ref':REF})
            method='infra-connection-pressure/v1' if service=='rds' and component=='anomaly_strength' else METHODS[component]
            components[component]={'value':1. if service=='payment-service' else 0.,'method':method,
                                   'evidence_refs':[identifier],'missing_reason':None}
        cards.append({'service':service,'components':components})
    available={f['id']:'2026-10-26T00:04:00Z' for f in facts}
    return facts,cards,available


def evaluate(facts=None,cards=None,available=None,**kwargs):
    correlation,group=context()
    if facts is None:facts,cards,available=fixtures()
    return evaluate_rca(correlation,group,facts,cards,POLICY,as_of=AS_OF,
        evidence_availability=available,source_refs=[REF],**kwargs)


def test_complete_known_support_has_unrenormalized_weighted_scores_and_never_action_permission():
    result=evaluate()
    assert result['ranking']==['payment-service']
    top=result['candidates'][0]
    assert top['score']==pytest.approx(.8) and top['coverage']==pytest.approx(.8)
    assert top['components']['temporal_precedence']['value'] is None
    assert result['margin']==pytest.approx(.8) and result['abstained'] is False
    assert result['actions_allowed'] is False and result['historical_emissions_verified'] is False
    validate_payload('rca-result.schema.json',result)


def test_foreign_log_pattern_cannot_enter_live_rca_method_admission():
    facts, cards, available = fixtures()
    payment = next(c for c in cards if c['service'] == 'payment-service')
    payment['components']['log_support']['method'] = 'native-log-pattern-presence/v1'
    with pytest.raises(ValueError, match='method/evidence'):
        evaluate(facts, cards, available)


def test_all_missing_candidates_remain_null_and_are_not_deleted_or_declared_healthy():
    result=evaluate([],[],{})
    assert {c['service'] for c in result['candidates']}==set(SERVICES)
    assert all(c['score'] is None and c['coverage']==0 for c in result['candidates'])
    assert result['ranking']==[] and result['margin'] is None and result['abstained']
    assert 'unobserved-dependency-candidates' in result['abstention_reasons']


def test_partial_support_does_not_redistribute_missing_weights_or_hide_unknown_arrival():
    facts,cards,available=fixtures()
    pay=next(c for c in cards if c['service']=='payment-service')
    pay['components']={k:unavailable('model-or-modality-unavailable') for k in COMPONENTS}
    pay['components']['anomaly_strength']={'value':.8,'method':'if-normalized-score/v1',
        'evidence_refs':['payment-service/anomaly_strength'],'missing_reason':None}
    available['payment-service/anomaly_strength']=None
    result=evaluate(facts,[pay],available)
    top=result['candidates'][0]
    assert top['score']==pytest.approx(.2) and top['coverage']==pytest.approx(.25)
    assert set(result['abstention_reasons'])>={'insufficient-score','insufficient-coverage','source-availability-unverified'}


def test_exact_ties_abstain_and_sort_deterministically_without_claiming_a_unique_root():
    facts,cards,available=fixtures()
    checkout=next(c for c in cards if c['service']=='checkout-service')
    for card in checkout['components'].values():
        if card['value'] is not None:card['value']=1.
    result=evaluate(facts,cards,available)
    assert result['ranking'][:2]==['checkout-service','payment-service'] and result['margin']==0
    assert 'insufficient-margin' in result['abstention_reasons']
    reordered=evaluate(list(reversed(facts)),list(reversed(cards)),dict(reversed(list(available.items()))))
    assert reordered==result


def test_observed_onset_uncertainty_controls_temporal_support_instead_of_the_supplied_score():
    c,g=context()
    assert temporal_precedence(c,c['groups'][0],'payment-service',POLICY) is None
    later=correlate([opening('payment-service'),opening('checkout-service',120)])
    group=later['groups'][0]
    assert temporal_precedence(later,group,'payment-service',POLICY)==1
    assert temporal_precedence(later,group,'checkout-service',POLICY)==0
    facts,cards,available=fixtures()
    for service in ('payment-service','checkout-service'):
        facts.append({'id':service+'/onset','type':'timeline','service':service,'observed_at':'2026-10-26T00:00:00Z',
            'fact':'Synthetic onset fixture','source_ref':REF});available[service+'/onset']='2026-10-26T00:04:00Z'
    pay=next(row for row in cards if row['service']=='payment-service')
    pay['components']['temporal_precedence']={'value':1.,'method':'disjoint-onset-intervals/v1',
        'evidence_refs':['payment-service/onset','checkout-service/onset'],'missing_reason':None}
    with pytest.raises(ValueError,match='onset uncertainty'):evaluate(facts,cards,available)


@pytest.mark.parametrize('mutation',['score','coverage','abstained','drop_unknown','unknown_reference','source_scope'])
def test_rehash_cannot_forge_rank_coverage_abstention_or_evidence_membership(mutation):
    result=evaluate()
    if mutation=='score':result['candidates'][0]['score']=1.
    elif mutation=='coverage':result['candidates'][0]['coverage']=1.
    elif mutation=='abstained':result['abstained']=True
    elif mutation=='drop_unknown':result['candidates'].pop()
    elif mutation=='unknown_reference':result['candidates'][0]['components']['log_support']['evidence_refs']=['invented']
    else:result['group_id']='a'*64
    result['result_id']=stable_id({k:v for k,v in result.items() if k!='result_id'})
    with pytest.raises(ValueError):validate_payload('rca-result.schema.json',result)


@pytest.mark.parametrize('mutation',['future_observation','future_available','clock_reversed','duplicate_fact','unallowed_source','different_candidate','missing_with_refs','boolean_value','nan'])
def test_invalid_input_is_rejected_before_scoring(mutation):
    facts,cards,available=fixtures()
    card=cards[0]['components']['log_support']
    if mutation=='future_observation':facts[0]['observed_at']='2026-10-26T02:00:00Z'
    elif mutation=='future_available':available[facts[0]['id']]='2026-10-26T02:00:00Z'
    elif mutation=='clock_reversed':available[facts[0]['id']]='2026-10-25T23:00:00Z'
    elif mutation=='duplicate_fact':facts.append(deepcopy(facts[0]))
    elif mutation=='unallowed_source':facts[0]['source_ref']='fixture://another-scope'
    elif mutation=='different_candidate':card['evidence_refs']=['payment-service/log_support']
    elif mutation=='missing_with_refs':card['value']=None
    elif mutation=='boolean_value':card['value']=True
    else:card['value']=math.nan
    with pytest.raises(ValueError):evaluate(facts,cards,available)


def test_rds_rule_is_explicit_and_cannot_impersonate_an_http_model():
    facts,cards,available=fixtures()
    rds=next(c for c in cards if c['service']=='rds')
    rds['components']['anomaly_strength']['method']='if-normalized-score/v1'
    next(f for f in facts if f['id']=='rds/anomaly_strength')['type']='model'
    with pytest.raises(ValueError):evaluate(facts,cards,available)


def test_rehashed_missing_evidence_cannot_be_promoted_to_a_supported_diagnosis():
    result=evaluate([],[],{})
    result['abstained']=False;result['abstention_reasons']=[]
    result['result_id']=stable_id({k:v for k,v in result.items() if k!='result_id'})
    with pytest.raises(ValueError,match='abstention'):
        validate_payload('rca-result.schema.json',result)


def test_static_topology_can_keep_unknown_time_without_inventing_an_event_clock():
    facts,cards,available=fixtures()
    facts.append({'id':'topology-context','type':'topology','service':None,'observed_at':None,
        'fact':'Configured snapshot context with no measured deployment time','source_ref':REF})
    available['topology-context']=None
    result=evaluate(facts,cards,available)
    assert next(f for f in result['evidence'] if f['id']=='topology-context')['observed_at'] is None
    facts[-1].update(type='metric',service='rds')
    with pytest.raises(ValueError,match='observed timestamp'):evaluate(facts,cards,available)


@pytest.mark.parametrize('mutation',['sum','missing','boolean','unknown','uncertainty'])
def test_invalid_policy_never_silently_changes_missing_weight_semantics(mutation):
    p=deepcopy(POLICY)
    if mutation=='sum':p['weights']['anomaly_strength']=.9
    elif mutation=='missing':p['weights'].pop('log_support')
    elif mutation=='boolean':p['minimum_score']=True
    elif mutation=='unknown':p['new']=1
    else:p['onset_uncertainty_seconds']=-1
    c,g=context();facts,cards,available=fixtures()
    with pytest.raises(ValueError):evaluate_rca(c,g,facts,cards,p,as_of=AS_OF,evidence_availability=available,source_refs=[REF])
