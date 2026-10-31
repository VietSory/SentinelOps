"""Browser verification for explicit software fixtures OR an actual incident API.

No HTTP incident writes. Never records tokens, browser traces or request headers.
Fixture mode proves UI behavior only; native mode requires an observed SLO trigger.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx


def fixture_envelope(incident, revision, after=0):
    events = [{'event_id':str(uuid5(NAMESPACE_URL,f'fixture/{incident}/{i}')),'incident_id':incident,'revision':i,'event_type':'incident.revised',
        'lifecycle_stage':'ESCALATED','actor':'fixture-ui-only','reason':'Software fixture, not observed source evidence',
        'recorded_at':'2026-01-01T00:00:00Z'} for i in range(1,revision+1)]
    page = events[:1] if after == 0 and revision>1 else [e for e in events if e['revision']>after]
    return {'schema_version':'1.0','incident_id':incident,'revision':revision,'recorded_at':'2026-01-01T00:00:00Z',
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


def exercise_fixtures(page, base, output, expect):
    token = 'fixture-browser-token-not-a-real-secret'*2
    outage = {'active':False}
    records = [{'incident_id':'INC-FIXTURE-A','revision':3,'state':'escalated','severity':'high','detected_at':'2026-01-01T00:00:00Z',
        'affected_services':['payment-service','checkout-service']},
        {'incident_id':'INC-FIXTURE-B','revision':1,'state':'incident','severity':'medium','detected_at':'2026-01-01T01:00:00Z',
        'affected_services':['inventory-service']}]
    def reply(route):
        url = urlsplit(route.request.url); query = parse_qs(url.query)
        if url.path == '/ready':
            route.fulfill(status=503,json={'status':'not_ready','dependencies':{'incident_store':not outage['active'],
                'model':False,'authentication':True},'demo_mode':False}); return
        if route.request.headers.get('authorization') != 'Bearer '+token:
            route.fulfill(status=401,json={'code':'unauthorized','message':'Denied','request_id':'fixture-request'}); return
        if outage['active']:
            route.fulfill(status=503,json={'code':'incident_store_unavailable','message':'Unavailable','request_id':'fixture-outage'}); return
        if url.path == '/v1/incidents':
            filtered = [row for row in records if (not query.get('state') or row['state']==query['state'][0]) and
                        (not query.get('service') or query['service'][0] in row['affected_services'])]
            offset = 1 if query.get('cursor') else 0
            route.fulfill(json={'schema_version':'1.0','items':filtered[offset:offset+1],
                'next_cursor':'fixture-next' if len(filtered)>offset+1 else None}); return
        if url.path.endswith('/context'):
            incident = url.path.split('/')[-2]
            route.fulfill(json=fixture_envelope(incident,int(query.get('revision',['3'])[0]),int(query.get('after_revision',['0'])[0]))); return
        route.fulfill(status=404,json={'code':'not_found','message':'Not found','request_id':'fixture-request'})
    page.route('**/ready',reply); page.route('**/v1/incidents**',reply)
    page.goto(base+'/ui/',wait_until='networkidle')
    page.locator('#api-token').focus(); page.keyboard.press('Tab')
    expect(page.get_by_role('button',name='Connect',exact=True)).to_be_focused()
    page.locator('#api-token').fill('wrong-token-value-that-is-long-enough')
    page.get_by_role('button',name='Connect',exact=True).click()
    expect(page.locator('#notice')).to_contain_text('Token was rejected')
    page.locator('#api-token').fill(token); page.get_by_role('button',name='Connect',exact=True).click()
    expect(page.locator('[data-testid=incident-row]')).to_have_count(1)
    expect(page.locator('#model-status')).to_have_text('Unavailable')
    assert page.locator('#api-token').input_value() == ''
    assert token not in page.locator('body').inner_text()
    assert page.evaluate('localStorage.length + sessionStorage.length') == 0
    page.locator('#load-more').click(); expect(page.locator('[data-testid=incident-row]')).to_have_count(2)
    page.locator('#state-filter').select_option('recovered'); page.get_by_role('button',name='Apply filters').click()
    expect(page.get_by_text('No incidents in this view',exact=True)).to_be_visible()
    page.locator('#state-filter').select_option(''); page.get_by_role('button',name='Apply filters').click()
    page.get_by_role('button',name='Open INC-FIXTURE-A, revision 3',exact=True).click()
    expect(page.locator('#detail-title')).to_have_text('INC-FIXTURE-A')
    expect(page.get_by_text('Diagnostic qualification not exposed',exact=True)).to_be_visible()
    page.locator('.reference').click()
    expect(page.locator('.evidence-body p')).to_have_text('<img src=x onerror="window.__evidence_xss=true">')
    assert page.evaluate('window.__evidence_xss') is None
    assert page.locator('#detail-pane img').count() == 0
    assert '1970' not in page.locator('#detail-pane').inner_text()
    assert page.locator('#detail-pane a[href^="javascript:"]').count() == 0
    page.get_by_role('button',name='Timeline',exact=True).focus(); page.keyboard.press('Enter')
    page.get_by_role('button',name='Load more revisions',exact=True).click()
    expect(page.locator('.timeline li')).to_have_count(3)
    page.locator('#revision-input').fill('1'); page.get_by_role('button',name='View',exact=True).click()
    expect(page.locator('#revision-input')).to_have_value('1')
    expect(page.locator('.timeline li')).to_have_count(0)  # switching revision returns to Overview
    page.get_by_role('button',name='Timeline',exact=True).click(); expect(page.locator('.timeline li')).to_have_count(1)
    page.screenshot(path=str(output/'fixture-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(output/'fixture-mobile.png'),full_page=True)
    outage['active'] = True; page.locator('#refresh').click()
    expect(page.locator('#list-error')).to_contain_text('unavailable')
    expect(page.locator('#store-status')).to_have_text('Unavailable')
    page.locator('#disconnect').click()
    expect(page.locator('#connect-title')).to_be_visible()
    assert page.locator('[data-testid=incident-row]').count() == 0
    assert page.locator('#detail-title').count() == 0
    return {'scope':'fixture-browser-software-test-not-source-or-research-evidence',
        'checks':['authentication','pagination','filters','revision-pinning','timeline-pagination','model-unavailable',
                  'unknown-context','XSS-safe-facts-and-references','missing-timestamp','mobile-layout','store-outage','logout-clears-data']}


def exercise_native(page, base, output, incident, token, expect):
    with httpx.Client(base_url=base,headers={'Authorization':'Bearer '+token},trust_env=False) as client:
        if not incident:
            response = client.get('/v1/incidents'); response.raise_for_status()
            items = response.json()['items']
            if not items: raise ValueError('native browser verification requires an actual persisted incident')
            incident = items[0]['incident_id']
        response = client.get('/v1/incidents/'+incident+'/context'); response.raise_for_status(); original = response.json()
        if not any(item['source']=='slo' for item in original['context']['trigger_sources']):
            raise ValueError('selected incident is not a telemetry-derived SLO incident')
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
    expect(page.locator('#model-status')).to_have_text('Unavailable')
    page.screenshot(path=str(output/'native-desktop.png'),full_page=True)
    page.get_by_role('button',name='Evidence ('+str(len(original['payload']['evidence']))+')',exact=True).click()
    expect(page.locator('.evidence-card')).to_have_count(len(original['payload']['evidence']))
    page.get_by_role('button',name='Timeline',exact=True).click()
    expect(page.locator('.timeline li').first).to_be_visible()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(output/'native-mobile.png'),full_page=True)
    return {'scope':'actual-source-backed-incident-API-browser-read','incident_id':incident,
        'revision':original['revision'],'evidence_items':len(original['payload']['evidence']),
        'source_refs':original['context']['source_refs'],'diagnostic_accuracy_claim':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:18000')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--fixtures',action='store_true')
    parser.add_argument('--incident-id')
    parser.add_argument('--token-file',type=Path,default=Path('artifacts/lane-b/api-token'))
    parser.add_argument('--browser-executable',default=os.getenv('SENTINELOPS_CHROME'),
                        help='Use an existing Chrome executable; otherwise use Playwright Chromium.')
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username is not None:
        raise ValueError('browser verification requires the explicit loopback API origin')
    args.output.mkdir(parents=True,exist_ok=False)
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
                os.environ.get('SENTINELOPS_API_TOKEN') or args.token_file.read_text().strip(),expect)
            if errors: raise ValueError('browser emitted unhandled script errors')
            result.update(status='PASS',page_errors=errors,
                verifier_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                verifier_worktree_dirty=bool(subprocess.check_output(['git','status','--porcelain'])))
            (args.output/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(result),flush=True)
        finally: browser.close()


if __name__ == '__main__': main()
