"""Browser verification for explicit software fixtures OR an actual incident API.

No HTTP incident writes. Never records tokens, browser traces or request headers.
Fixture mode proves UI behavior only; native mode requires an observed SLO trigger.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx
from sentinelops.telemetry.core import stable_id, timestamp


UI_ASSETS = (
    'app.js','client.js','workspace.js','styles.css',
    'deeplinks.js','filters.js','summary.js','proposalwatch.js',
    'deeplinks.css','filters.css','summary.css','proposalwatch.css',
)
UI_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
          "img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
FIXTURE_TOKEN = 'fixture-browser-token-not-a-real-secret'*2
FIXTURE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def fixture_records(now=None):
    """Shared synthetic feed; a supplied clock keeps local preview filters useful."""
    now = now or FIXTURE_TIME
    cases = [
        ('A', 3, 'escalated', 'high', 5, ['payment-service', 'checkout-service']),
        ('B', 1, 'incident', 'medium', 35, ['inventory-service']),
        ('C', 4, 'escalated', 'critical', 80, ['checkout-service']),
        ('D', 3, 'recovered', 'high', 180, ['payment-service']),
        ('E', 2, 'recovered', 'medium', 420, ['inventory-service']),
        ('F', 2, 'incident', 'critical', 720, ['payment-service', 'inventory-service']),
        ('G', 2, 'degrading', 'high', 1320, ['checkout-service']),
        ('H', 2, 'recovering', 'low', 1800, ['inventory-service']),
    ]
    return [{'incident_id':'INC-FIXTURE-'+name, 'revision':revision, 'state':state,
             'severity':severity, 'detected_at':timestamp(now-timedelta(minutes=age)),
             'affected_services':services}
            for name, revision, state, severity, age, services in cases]


def verify_ui_assets(client):
    for path in ('/ui/', *(f'/ui/assets/{name}' for name in UI_ASSETS)):
        for method in ('get','head'):
            response = getattr(client,method)(path)
            assert response.status_code == 200, f'{method.upper()} {path}: {response.status_code}'
            expected_type = 'text/html' if path == '/ui/' else 'text/css' if path.endswith('.css') else 'text/javascript'
            assert response.headers['content-type'].split(';')[0] == expected_type, path
            assert response.headers['content-security-policy'] == UI_CSP, path
            assert response.headers['x-content-type-options'] == 'nosniff', path
    for name in ('index.html','routes.py','private.js','private.css','..%2F..%2Fapi_settings.py'):
        for method in ('get','head'):
            response = getattr(client,method)('/ui/assets/'+name)
            assert response.status_code == 404, name
    return {'assets_checked':len(UI_ASSETS),'asset_methods':['GET','HEAD'],'strict_csp':True}


def fixture_envelope(incident, revision, after=0, *, now=None):
    events = [{'event_id':str(uuid5(NAMESPACE_URL,f'fixture/{incident}/{i}')),'incident_id':incident,'revision':i,'event_type':'incident.revised',
        'lifecycle_stage':'ESCALATED','actor':'fixture-ui-only','reason':'Software fixture, not observed source evidence',
        'recorded_at':'2026-01-01T00:00:00Z'} for i in range(1,revision+1)]
    page = events[:1] if after == 0 and revision>1 else [e for e in events if e['revision']>after]
    result = {'schema_version':'1.0','incident_id':incident,'revision':revision,'recorded_at':'2026-01-01T00:00:00Z',
        'context':{'availability':'UNKNOWN','lifecycle_stage':'ESCALATED','recovery_source':None,
            'trigger_sources':[{'source':'manual','signal_id':'fixture-ui','emitted_at':'2026-01-01T00:00:00Z',
                               'source_ref':'fixture://ui-only'}],
            'source_refs':['fixture://ui-only'],'config_refs':['fixture://ui-only']},
        'payload':{'schema_version':'1.0','incident_id':incident,'detected_at':'2026-01-01T00:00:00Z','state':'escalated',
            'severity':'high','affected_services':['payment-service','checkout-service'],'service_signals':[],
            'root_cause_ranking':[{'service':'payment-service','score':.4,'evidence_refs':['fixture-evidence-1']}],
            'evidence':[{'id':'fixture-evidence-1','type':'log','service':'payment-service','observed_at':None,
                'fact':'<img src=x onerror="window.__evidence_xss=true">','source_ref':'javascript:window.__evidence_xss=true'}]},
        'actor':'fixture-ui-only','reason':'Software fixture, not observed source evidence',
        'timeline':{'events':page,'next_after_revision':page[-1]['revision'] if page and page[-1]['revision']<revision else None}}
    record = next((row for row in fixture_records(now) if row['incident_id'] == incident), None)
    if record:
        result['payload'].update({key:deepcopy(record[key]) for key in
                                  ('detected_at','state','severity','affected_services')})
        service = record['affected_services'][0]
        result['payload']['root_cause_ranking'][0]['service'] = service
        result['payload']['evidence'][0]['service'] = service
        stage = {'escalated':'ESCALATED','recovered':'RECOVERED','recovering':'VERIFYING'}.get(record['state'],'OPEN')
        result['context']['lifecycle_stage'] = stage
        if record['state'] == 'recovered':
            result['context'].update(availability='ELIGIBLE', recovery_source='manual')
        result['context']['trigger_sources'][0]['emitted_at'] = record['detected_at']
        result['recorded_at'] = record['detected_at']
        for event in page:
            event.update(recorded_at=record['detected_at'], lifecycle_stage=stage)
    if incident == 'INC-FIXTURE-A':
        # This exact prefix is the UI clock contract, NOT a source-verification claim.
        result['payload']['evidence'].append({'id':'fixture-rca-clock','type':'timeline','service':None,
            'observed_at':timestamp((now or FIXTURE_TIME)-timedelta(seconds=30)),
            'fact':'Source-verified continuous RCA hypotheses; SOFTWARE FIXTURE ONLY, no actual source evidence.',
            'source_ref':'artifact://manifests/'+'f'*64+'.json'})
    return result


def fixture_explanation(incident, revision, *, now=None):
    """Explicit browser contract fixture, never source/model/provider evidence."""
    return {'schema_version':'1.0','incident_id':incident,'revision':revision,
        'generated_at':timestamp(now or FIXTURE_TIME),'provider':'deterministic-facts/v1','fallback':True,
        'payload':{'schema_version':'1.0','incident_id':incident,
            'summary':f'Software fixture for revision {revision}; hypotheses are not confirmed causes.',
            'claims':[{'text':'<img src=x onerror="window.__explanation_xss=true">',
                       'evidence_refs':['fixture-evidence-1']}]}}


def fixture_actions(incident, revision, cursor=None, *, now=None):
    reason='<img src=x onerror="window.__action_xss=true">'
    recorded = timestamp(now or FIXTURE_TIME)
    item={'observation_version':'action-observation/v1','action_id':('2' if cursor else '1')*64,
        'incident_id':incident,'incident_revision':2,'action_revision':3,'runbook':'payment-rollback-known-good/v1',
        'phase':'escalated','recorded_at':recorded,'updated_at':recorded,
        'reason':reason,'before_image':'localhost:5001/payment@sha256:'+'b'*64,
        'known_good_image':'localhost:5001/payment@sha256:'+'a'*64,
        'target':{'cluster_uid':'fixture-cluster','namespace':'sentinelops-b-actions','namespace_uid':'fixture-namespace',
                  'deployment':'payment-service','deployment_uid':'fixture-deployment','container':'payment'},
        'history':[{'revision':i,'phase':phase,'recorded_at':recorded,'reason':reason,
                    'event_hash':str(i)*64,'previous_hash':str(i-1)*64 if i>1 else None}
                   for i,phase in enumerate(('prepared','dispatching','escalated'),1)]}
    return {'observation_version':'action-list/v1','incident_id':incident,'incident_revision':revision,
        'items':[item] if revision>=2 else [],'next_cursor':'fixture-actions-2' if revision>=2 and not cursor else None}


def fixture_quarantine(action_id, *, now=None):
    item=fixture_actions('INC-FIXTURE-A',3,now=now)['items'][0]
    opened = timestamp(now or FIXTURE_TIME)
    observed = timestamp((now or FIXTURE_TIME)+timedelta(seconds=280))
    payload={'release_version':'manual-quarantine-release/v1','review_ref':'artifact://manifests/'+'d'*64+'.json',
        'action_id':action_id,'action_revision':3,'terminal_event_hash':'3'*64,'target_key':stable_id(item['target']),
        'opened_at':opened,'observed_at':observed,
        'verification_as_of':observed,'last_bucket_end':timestamp((now or FIXTURE_TIME)+timedelta(minutes=4)),
        'before_ref':'artifact://observations/'+'a'*64+'.json','after_ref':'artifact://observations/'+'b'*64+'.json',
        'health_ref':'artifact://manifests/'+'c'*64+'.json','dispatch_authorized':False,'recovery_accepted':False,'ier_mutated':False}
    return {'observation_version':'quarantine-status/v1','action_id':action_id,'action_revision':3,
        'action_phase':'escalated','quarantine_state':'released','resolution':{'status':'RELEASED','resolution_id':'d'*64,
            'action_id':action_id,'operator_actor':'<img src=x onerror=window.__quarantine_xss=true>',
            'recorded_at':observed,'payload':payload}}


def fixture_proposals(now=None):
    """Synthetic pending review shared by HTTP preview and browser checks.

    Freeze ``now`` for a preview session: polling must not renew its deadline.
    """
    now = now or FIXTURE_TIME
    incident = fixture_envelope('INC-FIXTURE-A',3,now=now)['payload']
    proposal = {'proposal_version':'runbook-proposal/v1','proposal_id':'a'*64,
        'incident_id':incident['incident_id'],'incident_revision':3,
        'runbook_id':'payment-bad-deploy-rollback','catalog_sha256':'c'*64,
        'executor_runbook':'payment-rollback-known-good/v1','decision':'oncall',
        'score':.95,'margin':.4,'symptoms':{'severity':incident['severity']},
        'created_at':timestamp(now-timedelta(seconds=30)),
        'expires_at':timestamp(now+timedelta(minutes=14)),
        'current_image':'localhost:5001/payment@sha256:'+'b'*64,
        'rollback_image':'localhost:5001/payment@sha256:'+'a'*64}
    return [{'proposal':proposal,'proposal_sha256':stable_id(proposal),'status':'pending',
        'approval':None,'outcome':None,'current_revision':3,'incident':incident,
        'review_images':{key:proposal[key] for key in ('current_image','rollback_image')}}]


def exercise_fixtures(page, base, output, expect):
    token = FIXTURE_TOKEN
    outage = {'active':False,'actions':False,'quarantine':False,'explanation':False,'proposals':False,
              'read_scope':False,'expired':False,'identity_provider':False,'model_available':False}
    # The two-row subset deliberately exercises pagination and empty state filters.
    records = fixture_records()[:2]
    def reply(route):
        assert route.request.method == 'GET', 'browser fixtures must not write to the API'
        url = urlsplit(route.request.url); query = parse_qs(url.query)
        if url.path == '/ready':
            ready = outage['model_available'] and not outage['active']
            route.fulfill(status=200 if ready else 503,json={'status':'ready' if ready else 'not_ready',
                'dependencies':{'incident_store':not outage['active'],'model':outage['model_available'],
                    'authentication':True},'demo_mode':False,'direct_inference':False}); return
        if route.request.headers.get('authorization') != 'Bearer '+token:
            route.fulfill(status=401,json={'code':'unauthorized','message':'Denied','request_id':'fixture-request'}); return
        if url.path == '/ready/incidents':
            if outage['read_scope']:
                route.fulfill(status=403,json={'code':'insufficient_scope','request_id':'fixture-scope'});return
            if outage['expired']:
                route.fulfill(status=401,json={'code':'unauthorized','request_id':'fixture-expired'});return
            if outage['identity_provider']:
                route.fulfill(status=503,json={'code':'authentication_unavailable','request_id':'fixture-identity'});return
        if outage['active']:
            route.fulfill(status=503,json={'code':'incident_store_unavailable','message':'Unavailable','request_id':'fixture-outage'}); return
        if url.path == '/ready/incidents':
            route.fulfill(json={'status':'ready','component':'incident-store'}); return
        if url.path == '/v1/incidents':
            filtered = [row for row in records if (not query.get('state') or row['state']==query['state'][0]) and
                        (not query.get('service') or query['service'][0] in row['affected_services'])]
            offset = 1 if query.get('cursor') else 0
            route.fulfill(json={'schema_version':'1.0','items':filtered[offset:offset+1],
                'next_cursor':'fixture-next' if len(filtered)>offset+1 else None}); return
        if url.path.endswith('/context'):
            incident = url.path.split('/')[-2]
            route.fulfill(json=fixture_envelope(incident,int(query.get('revision',['3'])[0]),int(query.get('after_revision',['0'])[0]))); return
        if url.path.endswith('/explanation'):
            assert route.request.method == 'GET' and 'revision' in query
            if outage['explanation']:
                route.fulfill(status=503,json={'code':'explanation_unavailable','request_id':'fixture-explanation-outage'});return
            route.fulfill(json=fixture_explanation(url.path.split('/')[-2],int(query['revision'][0])));return
        if url.path.endswith('/actions'):
            if outage['actions']:
                route.fulfill(status=503,json={'code':'action_store_unavailable','request_id':'fixture-action-outage'}); return
            route.fulfill(json=fixture_actions(url.path.split('/')[-2],int(query['revision'][0]),query.get('cursor',[None])[0])); return
        if url.path == '/v1/runbook-proposals':
            # Without this the proposals panel reached the real test server and,
            # depending on timing, added a second alert inside #detail-pane.
            assert route.request.method == 'GET' and query.get('limit') == ['100']
            if outage['proposals']:
                route.fulfill(status=503,json={'code':'proposal_store_unavailable','request_id':'fixture-proposal-outage'});return
            route.fulfill(json={'items':[],'has_more':False});return
        if url.path.endswith('/quarantine'):
            assert route.request.method=='GET'
            if outage['quarantine']:
                route.fulfill(status=503,json={'code':'stored_action_unavailable','request_id':'fixture-quarantine-outage'});return
            route.fulfill(json=fixture_quarantine(url.path.split('/')[-2]));return
        route.fulfill(status=404,json={'code':'not_found','message':'Not found','request_id':'fixture-request'})
    page.route('**/ready',reply);page.route('**/ready/incidents',reply)
    page.route('**/v1/incidents**',reply);page.route('**/v1/actions/**',reply)
    page.route('**/v1/runbook-proposals**',reply)
    page.emulate_media(color_scheme='dark')
    page.goto(base+'/ui/#lang=vi',wait_until='networkidle')
    expect(page.locator('html')).to_have_attribute('lang','en')
    expect(page.locator('html')).to_have_css('color-scheme','light')
    expect(page.locator('html')).to_have_css('background-color','rgb(255, 255, 255)')
    expect(page.get_by_role('heading',name='Incident workspace',exact=True)).to_be_visible()
    page.locator('#nav-explanation').click()
    expect(page.locator('#notice')).to_contain_text('Connect to your workspace, then choose an incident')
    expect(page.locator('#api-token')).to_be_focused()
    page.locator('#api-token').focus(); page.keyboard.press('Tab')
    expect(page.get_by_role('button',name='Connect',exact=True)).to_be_focused()
    page.locator('#api-token').fill('wrong-token-value-that-is-long-enough')
    page.get_by_role('button',name='Connect',exact=True).click()
    expect(page.locator('#notice')).to_contain_text('Token was rejected')
    page.locator('#api-token').fill(token); page.get_by_role('button',name='Connect',exact=True).click()
    expect(page.locator('[data-testid=incident-row]')).to_have_count(1)
    expect(page.locator('.incident-summary__card--loaded dd')).to_have_text('1')
    expect(page.locator('.incident-summary__metric--high dd')).to_have_text('1')
    expect(page.locator('#slot-filters .incident-filters')).to_have_count(1)
    expect(page.locator('#slot-summary .incident-summary')).to_have_count(1)
    expect(page.locator('#slot-deeplinks .incident-deeplinks')).to_have_count(1)
    expect(page.locator('#slot-proposalwatch > *')).to_have_count(1)
    expect(page.locator('#metric-loaded')).to_have_count(0)
    expect(page.locator('#model-status')).to_have_text('Unavailable')
    assert page.locator('#api-token').input_value() == ''
    assert token not in page.locator('body').inner_text()
    assert page.evaluate('localStorage.length + sessionStorage.length') == 0
    page.locator('#nav-actions').click()
    expect(page.locator('#notice')).to_contain_text('Choose an incident from the feed')
    page.locator('#load-more').click(); expect(page.locator('[data-testid=incident-row]')).to_have_count(2)
    expect(page.locator('.incident-summary__card--loaded dd')).to_have_text('2')
    search = page.get_by_role('searchbox',name='Search incident ID')
    search.fill('INC-FIXTURE-B')
    expect(page.locator('[data-testid=incident-row]')).to_have_count(1)
    expect(page.get_by_role('button',name='Open INC-FIXTURE-B, revision 1',exact=True)).to_be_visible()
    search.fill('');expect(page.locator('[data-testid=incident-row]')).to_have_count(2)
    page.get_by_role('combobox',name='Severity',exact=True).select_option('high')
    expect(page.locator('[data-testid=incident-row]')).to_have_count(1)
    page.get_by_role('combobox',name='Severity',exact=True).select_option('')
    expect(page.locator('[data-testid=incident-row]')).to_have_count(2)
    page.locator('#state-filter').select_option('recovered'); page.get_by_role('button',name='Apply filters').click()
    expect(page.get_by_text('No incidents in this view',exact=True)).to_be_visible()
    page.locator('#state-filter').select_option(''); page.get_by_role('button',name='Apply filters').click()
    page.get_by_role('button',name='Open INC-FIXTURE-A, revision 3',exact=True).click()
    expect(page.locator('#detail-title')).to_have_text('INC-FIXTURE-A')
    proposals = page.get_by_test_id('runbook-proposals')
    expect(proposals).to_contain_text('No proposals for this revision.')
    expect(proposals.get_by_role('alert')).to_have_count(0)
    expect(page.get_by_text('Diagnostic qualification not exposed',exact=True)).to_be_visible()
    page.locator('.reference').click()
    expect(page.locator('details[data-evidence-id="fixture-evidence-1"] .evidence-body p')).to_have_text('<img src=x onerror="window.__evidence_xss=true">')
    assert page.evaluate('window.__evidence_xss') is None
    assert page.locator('#detail-pane img').count() == 0
    assert '1970' not in page.locator('#detail-pane').inner_text()
    assert page.locator('#detail-pane a[href^="javascript:"]').count() == 0
    page.locator('#nav-explanation').focus();page.keyboard.press('Enter')
    expect(page.locator('#nav-explanation')).to_have_attribute('aria-current','page')
    expect(page.get_by_text('Deterministic fallback',exact=True)).to_be_visible()
    expect(page.locator('.explanation-summary')).to_contain_text('revision 3')
    expect(page.locator('.explanation-claim p')).to_have_text('<img src=x onerror="window.__explanation_xss=true">')
    assert page.evaluate('window.__explanation_xss') is None and page.locator('#detail-pane img').count()==0
    page.locator('.explanation-claim .reference').click()
    expect(page.locator('details[data-evidence-id="fixture-evidence-1"]')).to_have_attribute('open','')
    page.get_by_role('button',name='Explanation',exact=True).click()
    outage['explanation']=True;page.get_by_role('button',name='Reload explanation',exact=True).click()
    expect(page.locator('#detail-pane .detail-content [role=alert]')).to_contain_text('Explanation is unavailable')
    # Independent sections can fail together; each assertion must read its own alert.
    outage['proposals']=True;proposals.get_by_role('button',name='Reload proposals',exact=True).click()
    expect(proposals.get_by_role('alert')).to_contain_text('The proposal store is unavailable')
    expect(page.locator('#detail-pane [role=alert]')).to_have_count(2)
    expect(page.locator('#detail-pane .detail-content [role=alert]')).to_contain_text('Explanation is unavailable')
    outage['proposals']=False;proposals.get_by_role('button',name='Reload proposals',exact=True).click()
    expect(proposals.get_by_role('alert')).to_have_count(0)
    expect(proposals).to_contain_text('No proposals for this revision.')
    expect(page.locator('.explanation-summary')).to_have_count(0)
    expect(page.locator('#detail-title')).to_have_text('INC-FIXTURE-A')
    outage['explanation']=False;page.get_by_role('button',name='Load explanation',exact=True).click()
    expect(page.locator('.explanation-summary')).to_contain_text('revision 3')
    expect(page.get_by_role('button',name='Reload explanation',exact=True)).to_be_focused()
    page.get_by_role('button',name='Timeline',exact=True).focus(); page.keyboard.press('Enter')
    page.get_by_role('button',name='Load more revisions',exact=True).click()
    expect(page.locator('.timeline li')).to_have_count(3)
    page.locator('#nav-actions').focus(); page.keyboard.press('Enter')
    expect(page.locator('#nav-actions')).to_have_attribute('aria-current','page')
    expect(page.locator('.evidence-card')).to_have_count(1)
    page.locator('.evidence-card summary').click()
    expect(page.locator('.evidence-body > p').first).to_have_text('<img src=x onerror="window.__action_xss=true">')
    assert page.evaluate('window.__action_xss') is None and page.locator('#detail-pane img').count()==0
    page.get_by_role('button',name='View quarantine status',exact=True).focus();page.keyboard.press('Enter')
    expect(page.get_by_text('Quarantine released by operator',exact=True)).to_be_visible()
    expect(page.get_by_role('button',name='Reload quarantine status',exact=True)).to_be_focused()
    expect(page.get_by_text('A released lock does not establish recovery or authorize another dispatch.',exact=False)).to_be_visible()
    assert page.evaluate('window.__quarantine_xss') is None and page.locator('#detail-pane img').count()==0
    outage['quarantine']=True;page.get_by_role('button',name='Reload quarantine status',exact=True).click()
    expect(page.locator('#detail-pane .detail-content [role=alert]')).to_contain_text('could not be verified')
    expect(page.get_by_text('Quarantine released by operator',exact=True)).to_have_count(0)
    outage['quarantine']=False;page.get_by_role('button',name='View quarantine status',exact=True).click()
    expect(page.get_by_text('Quarantine released by operator',exact=True)).to_be_visible()
    page.get_by_role('button',name='Load more actions',exact=True).click()
    expect(page.locator('.evidence-card')).to_have_count(2)
    page.screenshot(path=str(output/'fixture-actions-desktop.png'),full_page=True)
    outage['actions']=True;page.get_by_role('button',name='Reload action history',exact=True).click()
    expect(page.locator('#detail-pane .detail-content [role=alert]')).to_contain_text('Action history is unavailable')
    expect(page.locator('#detail-title')).to_have_text('INC-FIXTURE-A')
    outage['actions']=False
    page.locator('#revision-input').fill('1'); page.get_by_role('button',name='View',exact=True).click()
    expect(page.locator('#revision-input')).to_have_value('1')
    expect(page.locator('.timeline li')).to_have_count(0)  # switching revision returns to Overview
    page.get_by_role('button',name='Actions',exact=True).click()
    expect(page.get_by_text('No action records were returned through this revision.',exact=False)).to_be_visible()
    page.get_by_role('button',name='Timeline',exact=True).click(); expect(page.locator('.timeline li')).to_have_count(1)
    page.get_by_role('button',name='Explanation',exact=True).click()
    expect(page.locator('.explanation-summary')).to_contain_text('revision 1')
    page.screenshot(path=str(output/'fixture-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.get_by_role('button',name='Open INC-FIXTURE-A, revision 3',exact=True).click()
    expect(page.locator('#revision-input')).to_have_value('3')
    expect(page.locator('#detail-title')).to_be_focused()
    expect(page.locator('#detail-title')).to_be_in_viewport()
    page.get_by_role('button',name='Explanation',exact=True).click()
    expect(page.locator('.explanation-summary')).to_contain_text('revision 3')
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(output/'fixture-mobile.png'),full_page=True)
    page.get_by_role('button',name='Actions',exact=True).click()
    expect(page.locator('.evidence-card')).to_have_count(1)
    page.locator('.evidence-card summary').click()
    page.get_by_role('button',name='View quarantine status',exact=True).click()
    expect(page.get_by_text('Quarantine released by operator',exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(output/'fixture-actions-mobile.png'),full_page=True)
    outage['active'] = True; page.locator('#refresh').click()
    expect(page.locator('#list-error')).to_contain_text('unavailable')
    expect(page.locator('.incident-summary__card--loaded dd')).to_have_text('—')
    expect(page.locator('#store-status')).to_have_text('Unavailable')
    page.locator('#disconnect').click()
    expect(page.locator('#connect-title')).to_be_visible()
    assert page.locator('[data-testid=incident-row]').count() == 0
    assert page.locator('#detail-title').count() == 0
    expect(page.locator('#slot-proposalwatch > *')).to_have_count(0)
    outage['active'] = False
    outage['model_available'] = True  # Readiness fixture only: no scores or approved model are fabricated.
    for problem, notice in (('identity_provider',None),('read_scope','cannot read incidents'),('expired','Token was rejected')):
        page.locator('#api-token').fill(token);page.get_by_role('button',name='Connect',exact=True).click()
        expect(page.locator('[data-testid=incident-row]')).to_have_count(1)
        expect(page.locator('#model-status')).to_have_text('Available')
        page.get_by_role('button',name='Open INC-FIXTURE-A, revision 3',exact=True).click()
        expect(page.locator('#detail-title')).to_have_text('INC-FIXTURE-A')
        page.get_by_role('button',name='Explanation',exact=True).click()
        expect(page.locator('.explanation-summary')).to_contain_text('revision 3')
        outage[problem] = True
        # Trigger only the visible-session heartbeat, not list refresh. Public /ready
        # still reports configured authentication, so it cannot detect these failures.
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        if notice:
            expect(page.locator('#notice')).to_contain_text(notice)
            expect(page.locator('#connect-title')).to_be_visible()
            expect(page.locator('[data-testid=incident-row]')).to_have_count(0)
            expect(page.locator('#detail-title')).to_have_count(0)
            expect(page.locator('.explanation-summary')).to_have_count(0)
            expect(page.locator('#slot-proposalwatch > *')).to_have_count(0)
        else:
            expect(page.locator('#readiness-error')).to_contain_text('Authentication is unavailable')
            expect(page.locator('#store-status')).to_have_text('Unknown')
            expect(page.locator('#detail-title')).to_have_text('INC-FIXTURE-A')
            page.locator('#disconnect').click()
        outage[problem] = False
        assert page.evaluate('localStorage.length + sessionStorage.length') == 0
        assert token not in page.locator('body').inner_text()
    return {'scope':'fixture-browser-software-test-not-source-or-research-evidence',
        'checks':['authentication','pagination','filters','revision-pinning','timeline-pagination','model-unavailable',
                  'unknown-context','XSS-safe-facts-and-references','missing-timestamp','mobile-layout','store-outage','logout-clears-data',
                  'read-only-action-audit','action-pagination','action-revision-scope','action-outage','XSS-safe-action-reason',
                  'manual-quarantine-receipt-not-recovery','quarantine-outage','quarantine-keyboard-focus','XSS-safe-operator-identity',
                  'explanation-revision-pinning','explanation-fallback','explanation-outage-clears-text',
                  'explanation-reference-navigation','XSS-safe-explanation','explanation-keyboard-focus',
                  'empty-runbook-proposals','independent-proposal-and-explanation-outages',
                  'protected-session-heartbeat','permission-loss-clears-private-data','expired-token-clears-private-data',
                  'identity-provider-outage-keeps-records-unknown-readiness','configured-model-availability']}


def exercise_native(page, base, output, incident, token, expect):
    with httpx.Client(base_url=base,headers={'Authorization':'Bearer '+token},trust_env=False) as client:
        readiness = client.get('/ready')
        if readiness.status_code not in (200,503):
            readiness.raise_for_status()
        model_available = readiness.json().get('dependencies',{}).get('model')
        if type(model_available) is not bool:
            raise ValueError('native UI verification requires explicit model availability')
        if not incident:
            response = client.get('/v1/incidents'); response.raise_for_status()
            items = response.json()['items']
            if not items: raise ValueError('native browser verification requires an actual persisted incident')
            incident = items[0]['incident_id']
        response = client.get('/v1/incidents/'+incident+'/context'); response.raise_for_status(); original = response.json()
        if not any(item['source']=='slo' for item in original['context']['trigger_sources']):
            raise ValueError('selected incident is not a telemetry-derived SLO incident')
        explanation_response=client.get('/v1/incidents/'+incident+'/explanation',params={'revision':original['revision']})
        explanation_response.raise_for_status();explanation=explanation_response.json()
        from sentinelops.contracts import validate_payload
        validate_payload('explanation-response.schema.json',explanation)
        assert explanation['revision']==original['revision'] and explanation['fallback']
        assert explanation_response.headers['x-incident-revision']==str(original['revision'])
        known={item['id'] for item in original['payload']['evidence']}
        assert all(set(claim['evidence_refs'])<=known for claim in explanation['payload']['claims'])
    page.goto(base+'/ui/',wait_until='networkidle')
    page.locator('#api-token').fill(token); page.get_by_role('button',name='Connect',exact=True).click()
    expect(page.locator('[data-testid=incident-row]').first).to_be_visible()
    # Select the exact API revision through the supported deep link, even if it
    # is not on the first current-state list page.
    page.evaluate('value => history.replaceState(null,"",value)',
                  '#incident='+incident+'&revision='+str(original['revision']))
    page.locator('#disconnect').click()
    page.goto(base+'/ui/#incident='+incident+'&revision='+str(original['revision']))
    page.locator('#api-token').fill(token); page.get_by_role('button',name='Connect',exact=True).click()
    expect(page.locator('#detail-title')).to_have_text(incident)
    expect(page.locator('#revision-input')).to_have_value(str(original['revision']))
    expect(page.locator('#model-status')).to_have_text('Available' if model_available else 'Unavailable')
    page.get_by_role('button',name='Explanation',exact=True).click()
    expect(page.get_by_text('Deterministic fallback',exact=True)).to_be_visible()
    expect(page.locator('.explanation-summary')).to_have_text(explanation['payload']['summary'])
    expect(page.locator('.explanation-claim')).to_have_count(len(explanation['payload']['claims']))
    page.screenshot(path=str(output/'native-explanation-desktop.png'),full_page=True)
    page.screenshot(path=str(output/'native-desktop.png'),full_page=True)
    page.get_by_role('button',name='Evidence ('+str(len(original['payload']['evidence']))+')',exact=True).click()
    expect(page.locator('.evidence-card')).to_have_count(len(original['payload']['evidence']))
    page.get_by_role('button',name='Timeline',exact=True).click()
    expect(page.locator('.timeline li').first).to_be_visible()
    page.set_viewport_size({'width':390,'height':844})
    page.get_by_role('button',name='Explanation',exact=True).click()
    expect(page.locator('.explanation-summary')).to_have_text(explanation['payload']['summary'])
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(output/'native-mobile.png'),full_page=True)
    with httpx.Client(base_url=base,headers={'Authorization':'Bearer '+token},trust_env=False) as client:
        after=client.get('/v1/incidents/'+incident+'/context',params={'revision':original['revision']})
        after.raise_for_status();assert after.json()==original
    return {'scope':'actual-source-backed-incident-API-browser-read','incident_id':incident,
        'revision':original['revision'],'evidence_items':len(original['payload']['evidence']),
        'model_available':model_available,
        'explanation_provider':explanation['provider'],'explanation_claims':len(explanation['payload']['claims']),
        'explanation_refs_in_pinned_ier':True,'pinned_ier_unchanged':True,
        'source_refs':original['context']['source_refs'],'diagnostic_accuracy_claim':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:18000')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--fixtures',action='store_true')
    parser.add_argument('--incident-id')
    parser.add_argument('--token-file',type=Path,default=Path('artifacts/lane-b/operator-token'))
    parser.add_argument('--browser-executable',default=os.getenv('SENTINELOPS_CHROME'),
                        help='Use an existing Chrome executable; otherwise use Playwright Chromium.')
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username is not None:
        raise ValueError('browser verification requires the explicit loopback API origin')
    args.output.mkdir(parents=True,exist_ok=False)
    with httpx.Client(base_url=args.base_url,trust_env=False) as client:
        asset_checks = verify_ui_assets(client)
    if 'PLAYWRIGHT_NODEJS_PATH' not in os.environ and shutil.which('node'):
        os.environ['PLAYWRIGHT_NODEJS_PATH'] = shutil.which('node')
    from playwright.sync_api import sync_playwright, expect
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser_executable,headless=True,
            args=['--no-sandbox','--disable-dev-shm-usage'])
        try:
            page = browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
            page.on('pageerror',lambda error:errors.append(type(error).__name__))
            result = exercise_fixtures(page,args.base_url.rstrip('/'),args.output,expect) if args.fixtures else exercise_native(
                page,args.base_url.rstrip('/'),args.output,args.incident_id,
                os.environ.get('SENTINELOPS_OPERATOR_TOKEN') or os.environ.get('SENTINELOPS_API_TOKEN') or
                args.token_file.read_text().strip(),expect)
            if errors: raise ValueError('browser emitted unhandled script errors')
            result.update(status='PASS',page_errors=errors,asset_checks=asset_checks,
                verifier_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                verifier_worktree_dirty=bool(subprocess.check_output(['git','status','--porcelain'])))
            (args.output/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(result),flush=True)
        finally: browser.close()


if __name__ == '__main__': main()
