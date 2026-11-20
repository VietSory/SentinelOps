"""Browser integration using intercepted APIs and signed, test-only OIDC tokens.

No production requests, credentials, fault injection or remediation are used.
"""
import argparse
import base64
import copy
from datetime import datetime, timezone
import json
import re
import os
from pathlib import Path
import shutil
import sys
import time
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
os.environ.setdefault('PLAYWRIGHT_NODEJS_PATH', shutil.which('node'))
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes
from playwright.sync_api import sync_playwright, expect
from scripts.check_operator_ui import fixture_records, fixture_envelope, fixture_proposals, fixture_actions, fixture_explanation

ORIGIN='https://live-ui.test'
EXPLANATION_CASES = (
    ('INC-FIXTURE-B', 'fallback', 'fallback'),
    ('INC-FIXTURE-C', 'abstained', 'abstained'),
    ('INC-FIXTURE-A', 'generated', 'ai'),
)
NARRATIVE_MARKUP = '<span data-rca-fixture="literal">Untrusted markup fixture</span>'
AI_NARRATIVE = (
    ('Vấn đề:', 'Dịch vụ thanh toán trả lỗi khi xử lý giao dịch.'),
    ('Bằng chứng:', f'Trace ghi nhận lỗi 503. {NARRATIVE_MARKUP} ' + 'fixture' * 125),
    ('Chưa chắc chắn:', 'Cần thêm dữ liệu để xác định nguyên nhân gốc.'),
)
FALLBACK_NARRATIVE = (
    'Software fixture for the ranked hypothesis.',
    'Recorded spans returned errors.',
    'The cause is still unconfirmed.',
)
CONFIG={'mode':'oidc','issuer':'https://issuer.test/pool','client_id':'test-client',
        'domain':'https://login.test','jwks_url':'https://issuer.test/pool/.well-known/jwks.json',
        'scopes':['openid','sentinelops/incidents.read','sentinelops/remediation.approve'],
        'read_scope':'sentinelops/incidents.read','approval_scope':'sentinelops/remediation.approve',
        'callback_path':'/ui/callback'}
KEY=rsa.generate_private_key(public_exponent=65537,key_size=2048)
def b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')
def encoded(obj):return b64(json.dumps(obj,separators=(',',':')).encode())
def signed(claims):
    data=encoded({'alg':'RS256','kid':'fixture'})+'.'+encoded(claims)
    return data+'.'+b64(KEY.sign(data.encode(),padding.PKCS1v15(),hashes.SHA256()))
numbers=KEY.public_key().public_numbers()
JWK={'kty':'RSA','kid':'fixture','alg':'RS256','use':'sig',
     'n':b64(numbers.n.to_bytes((numbers.n.bit_length()+7)//8,'big')),
     'e':b64(numbers.e.to_bytes((numbers.e.bit_length()+7)//8,'big'))}

def explanation_fixture(incident, revision, now, mode):
    explanation = fixture_explanation(incident, revision, now=now, mode=mode)
    root = explanation['payload']['root_cause']
    if mode != 'fallback':
        explanation['provider'] = 'openai-responses/v1/software-fixture'
    if mode == 'generated':
        root['score'] = 0.73
        root['explanation'] = '\n'.join(f'{label} {content}' for label, content in AI_NARRATIVE)
    elif mode == 'fallback':
        root['explanation'] = ' '.join(FALLBACK_NARRATIVE)
    return explanation


def assert_no_horizontal_overflow(page):
    layout = page.evaluate('''() => ({
        width: window.innerWidth,
        scrollWidth: document.documentElement.scrollWidth,
        overflowing: [...document.querySelectorAll('body *')]
            .filter(element => element.getBoundingClientRect().right > window.innerWidth + 1)
            .slice(-15).map(element => ({ tag: element.tagName, text: element.textContent.slice(0, 80) }))
    })''')
    assert layout['scrollWidth'] <= layout['width'], layout


def check_incident_list(page, records, width):
    rows = page.get_by_test_id('live-incident-row')
    expect(rows).to_have_count(len(records))
    assert page.evaluate('Intl.DateTimeFormat().resolvedOptions().timeZone') == 'UTC'
    newest = max(records, key=lambda record: datetime.fromisoformat(record['detected_at']))
    assert records[0]['incident_id'] != newest['incident_id']
    expect(rows.first).to_contain_text(newest['incident_id'])
    for record in records:
        row = rows.filter(has_text=record['incident_id'])
        detected = datetime.fromisoformat(record['detected_at']).astimezone(ZoneInfo('Asia/Ho_Chi_Minh'))
        expected_time = detected.strftime('%m/%d, %H:%M (GMT+7)')
        expect(row.get_by_text(expected_time, exact=True)).to_be_visible()
    for incident, _, _ in EXPLANATION_CASES:
        expect(rows.filter(has_text=incident).get_by_test_id('rca-confidence')).to_be_visible()
    if width == 390:
        assert_no_horizontal_overflow(page)


def set_locale(page, locale):
    button = page.get_by_role('button', name=re.compile(f'^{locale}$', re.I))
    button.click()
    expect(button).to_have_attribute('aria-pressed', 'true')


def assert_provider_hidden(page, view):
    expect(page.get_by_test_id('ai-rca-provider')).to_have_count(0)
    leaks = page.evaluate('''() => {
        const values = [
            ['visible text', document.body.innerText],
            ...[...document.querySelectorAll('title')].map(element => ['title', element.textContent]),
            ...['title', 'aria-label'].flatMap(attribute =>
                [...document.querySelectorAll(`[${attribute}]`)]
                    .map(element => [attribute, element.getAttribute(attribute)])),
        ];
        return values.flatMap(([field, value]) => {
            const match = /openai/i.exec(value);
            return match ? [{field, text: value.slice(Math.max(0, match.index - 25), match.index + 40)}] : [];
        });
    }''')
    assert not leaks, f'Provider name leaked in {view}: {leaks}'


def check_narrative_lines(narrative, root, source, locale):
    lines = narrative.get_by_test_id('ai-rca-line')
    if source == 'ai':
        expect(lines).to_have_count(len(AI_NARRATIVE))
        for index, (label, content) in enumerate(AI_NARRATIVE):
            line = lines.nth(index)
            expect(line).to_be_visible()
            if locale == 'vi':
                expect(line).to_have_attribute('data-label', label)
            expect(line).to_contain_text(content)
    elif source == 'fallback':
        expect(lines.nth(1)).to_be_visible()
        expect(narrative.locator('[data-testid="ai-rca-line"]:not([data-label=""])')).to_have_count(0)
        for sentence in FALLBACK_NARRATIVE:
            expect(narrative).to_contain_text(sentence)
    else:
        expect(narrative).to_contain_text(root['explanation'])


def check_explanation_panel(page, explanation, mode, source, width, output_dir):
    panel = page.get_by_test_id('ai-rca-panel')
    expect(panel).to_be_visible()
    state = panel.get_by_test_id('ai-rca-state')
    expect(state).to_be_visible()
    state_pattern = {
        'ai': r'\bAI (?:analysis|generated)\b|\bAI-generated\b',
        'fallback': r'\bfallback\b',
        'abstained': r'\babstain(?:ed)?\b',
    }[source]
    expect(state).to_contain_text(re.compile(state_pattern, re.I))
    badge = panel.get_by_test_id('rca-confidence')
    expect(badge).to_be_visible()
    expect(badge).to_have_attribute('data-source', source)
    root = explanation['payload']['root_cause']
    narrative = panel.get_by_test_id('ai-rca-narrative')
    expect(narrative).to_be_visible()
    check_narrative_lines(narrative, root, source, 'en')
    expect(page.locator('[data-rca-fixture]')).to_have_count(0)
    assert_provider_hidden(page, f'{mode} detail (EN)')
    expect(panel.get_by_text('not a probability').first).to_be_visible()
    if root['score'] is None:
        expect(badge).not_to_contain_text(re.compile(r'\d+\s*%'))
    else:
        expect(badge).to_contain_text(f"{round(root['score'] * 100)}%")
    if width == 390:
        assert_no_horizontal_overflow(page)
    set_locale(page, 'vi')
    expect(panel.get_by_text('không phải xác suất').first).to_be_visible()
    check_narrative_lines(narrative, root, source, 'vi')
    expect(page.locator('[data-rca-fixture]')).to_have_count(0)
    assert_provider_hidden(page, f'{mode} detail (VI)')
    if width == 390:
        assert_no_horizontal_overflow(page)
    panel.screenshot(path=output_dir / f'ai-rca-{mode}-{width}.png')
    set_locale(page, 'en')
    expect(panel.get_by_text('not a probability').first).to_be_visible()


def scenario(browser, width, output_dir, read_only=False):
    now=datetime.now(timezone.utc)
    records = list(reversed(fixture_records(now)))
    revisions = {record['incident_id']: record['revision'] for record in records}
    explanations = {
        (incident, revisions[incident]): explanation_fixture(incident, revisions[incident], now, mode)
        for incident, mode, _ in EXPLANATION_CASES
    }
    incoming = EXPLANATION_CASES[-1][0]
    incoming_visible = False
    proposal=fixture_proposals(now)[0]
    fixture={'authorize':None,'access':None,'posts':[],'stale':False,'missing':False,'outage':False,'lose_reply':False}
    errors=[]
    context=browser.new_context(viewport={'width':width,'height':900},service_workers='block',timezone_id='UTC')
    page=context.new_page()
    page.set_default_timeout(8000)
    page.on('pageerror',lambda error:errors.append(str(error)))
    def handler(route):
        request=route.request;url=urlsplit(request.url);query=parse_qs(url.query)
        if request.url.startswith(CONFIG['domain']+'/oauth2/authorize?'):
            fixture['authorize']=query
            route.fulfill(content_type='text/html',body='<p>Test-only OAuth authorization</p>');return
        if request.url==CONFIG['domain']+'/oauth2/token':
            assert request.method=='POST' and 'client_secret' not in request.post_data
            seconds=int(time.time());nonce=fixture['authorize']['nonce'][0]
            access=signed({'iss':CONFIG['issuer'],'client_id':CONFIG['client_id'],'sub':'fixture-sub',
                           'token_use':'access','exp':seconds+300,'scope':CONFIG['read_scope']+('' if read_only else ' '+CONFIG['approval_scope'])})
            fixture['access']=access
            identity=signed({'iss':CONFIG['issuer'],'aud':CONFIG['client_id'],'sub':'fixture-sub',
                             'token_use':'id','iat':seconds,'exp':seconds+300,'nonce':nonce})
            route.fulfill(json={'token_type':'Bearer','access_token':access,'id_token':identity,'expires_in':300});return
        if request.url==CONFIG['jwks_url']:
            route.fulfill(json={'keys':[JWK]});return
        assert url.scheme+'://'+url.netloc==ORIGIN, 'Unexpected external request: '+url.netloc
        path=url.path
        if path in ('/ui/','/ui/callback'):
            route.fulfill(path=ROOT/'web/dist/index.html',content_type='text/html');return
        if path.startswith('/ui/assets/'):
            name=path.rsplit('/',1)[1]
            source=ROOT/'web/dist/assets'/name
            if name in ('client.js','workspace.js'):source=ROOT/'src/sentinelops/ui/assets'/name
            if source.is_file():route.fulfill(path=source,content_type='text/css' if name.endswith('.css') else 'text/javascript')
            else:route.fulfill(status=404)
            return
        if path=='/ui/auth-config':route.fulfill(json=CONFIG);return
        if path=='/ready':route.fulfill(status=403,json={'code':'http_error'});return
        if path.startswith('/v1/') or path=='/ready/incidents':
            assert request.headers.get('authorization')=='Bearer '+fixture['access']
        if path=='/ready/incidents':route.fulfill(json={'status':'ready','component':'incident-store'});return
        if path=='/v1/observability/overview':
            at=time.time();times=[at-86400+i*300 for i in range(289)]
            route.fulfill(json={'schema_version':'live-observability/v1','as_of':now.isoformat(),'environment':'dev',
                'services':[{'service':name,'p95_ms':950 if name=='payment-service' else 40,'error_pct':18 if name=='payment-service' else 0,'success_pct':82 if name=='payment-service' else 100,'rps':5} for name in ['checkout-service','payment-service','inventory-service']],
                'series':{'p95_ms':{'payment-service':[[t,950] for t in times],'checkout-service':[[t,980] for t in times],'inventory-service':[[t,8] for t in times]},'error_pct':{'payment-service':[[t,18] for t in times]}},'unavailable':[]});return
        if path.startswith('/v1/observability/'):
            at=int((time.time()-180)*1000000);trace='d'*32
            def span(id,parent,process,operation,duration,code,error=False,extra=[]):
                return {'traceID':trace,'spanID':id,'processID':process,'operationName':operation,'startTime':at,'duration':duration,
                    'references':[{'refType':'CHILD_OF','traceID':trace,'spanID':parent}] if parent else [],
                    'tags':[{'key':'http.status_code','type':'int64','value':code},{'key':'error','type':'bool','value':error}]+extra}
            data={'traceID':trace,'processes':{'p1':{'serviceName':'checkout-service'},'p2':{'serviceName':'payment-service'},'p3':{'serviceName':'inventory-service'}},
                  'spans':[span('1'*16,None,'p1','checkout-service POST',1000000,502,True),span('2'*16,'1'*16,'p2','payment-service POST',950000,503,True),
                           span('3'*16,'2'*16,'p2','SELECT payment',900000,0,True,[{'key':'db.system','type':'string','value':'postgresql'}]),span('4'*16,'1'*16,'p3','inventory-service POST',10000,200)]}
            route.fulfill(json={'schema_version':'live-traces/v1','association':'window','items':[data]});return
        if path=='/v1/incidents':
            rows = [record for record in records
                    if (incoming_visible or record['incident_id'] != incoming)
                    and (not query.get('state') or record['state'] == query['state'][0])
                    and (not query.get('service') or query['service'][0] in record['affected_services'])]
            route.fulfill(json={'schema_version':'1.0','items':rows,'next_cursor':None});return
        if path.endswith('/context'):
            incident=path.split('/')[3];revision=int(query.get('revision',[revisions[incident]])[0]);after=int(query.get('after_revision',[0])[0])
            route.fulfill(json=fixture_envelope(incident,revision,after,now=now));return
        if path.endswith('/actions'):
            incident=path.split('/')[3];revision=int(query['revision'][0])
            route.fulfill(status=503,json={'code':'action_store_unavailable'});return
        if path.endswith('/explanation'):
            incident = path.split('/')[3]
            revision = int(query['revision'][0])
            explanation = explanations.get((incident, revision))
            route.fulfill(json=explanation or fixture_explanation(incident, revision, now=now))
            return
        if path=='/v1/runbook-proposals':
            if fixture['outage']:route.fulfill(status=503,json={'code':'proposal_store_unavailable'});return
            item=copy.deepcopy(proposal)
            if fixture['stale']:item.update(status='stale',current_revision=4)
            if fixture['missing']:item['review_images']=None
            route.fulfill(json={'items':[item] if not query.get('incident_id') or query['incident_id'][0]==item['proposal']['incident_id'] else [],'has_more':False});return
        if path.endswith('/decision'):
            assert not read_only and request.method=='POST'
            body=request.post_data_json
            assert set(body)=={'decision','reason'} and body['reason'].strip()
            assert request.headers['if-match']=='"'+proposal['proposal_sha256']+'"'
            fixture['posts'].append(body)
            if fixture['lose_reply']:route.abort('failed');return
            proposal.update(status='approved' if body['decision']=='approve' else 'engineer',approval={
                'decision':body['decision'],'reason':body['reason'],'approved_by':'fixture-server-actor',
                'proposal_sha256':proposal['proposal_sha256'],'approved_at':now.isoformat()})
            route.fulfill(status=201,json=proposal);return
        route.fulfill(status=404,json={'code':'not_found'})
    context.route('**/*',handler)
    page.goto(ORIGIN+'/ui/?lang=en',wait_until='networkidle')
    expect(page.get_by_role('button',name='Sign in with Cognito',exact=True)).to_be_visible()
    assert not page.get_by_test_id('live-incident-row').count()
    page.get_by_role('button',name='Sign in with Cognito',exact=True).click()
    page.wait_for_url(CONFIG['domain']+'/oauth2/authorize?*')
    params=fixture['authorize'];assert params['code_challenge_method']==['S256']
    assert params['redirect_uri']==[ORIGIN+'/ui/callback']
    # This real navigation exercises pagehide: the PKCE transaction must survive.
    page.goto(ORIGIN+'/ui/callback?code=fixture-code&state='+params['state'][0],wait_until='networkidle')
    expect(page.get_by_test_id('live-incident-row')).to_have_count(5)
    expect(page.get_by_role('heading',name='24h Incident & Latency Trend',exact=True)).to_be_visible()
    expect(page.get_by_role('heading',name='Core Services Health',exact=True)).to_be_visible()
    assert page.locator('.recharts-wrapper').count()>0
    page.screenshot(path=f'/tmp/sentinelops-restored-dashboard-{width}.png',full_page=True)
    def nav(label):
        control=page.get_by_role('navigation',name='Primary navigation',exact=True).get_by_role('button',name=re.compile('^'+re.escape(label)+r'(?:\s|$)'))
        if not control.is_visible():page.get_by_role('button',name='Open navigation menu',exact=True).click()
        control.click()
        if width < 1024:
            expect(page.get_by_role('button',name='Close navigation menu',exact=True)).to_have_count(0)
    nav('Evidence & Trace')
    expect(page.get_by_test_id('live-service-map')).to_be_visible()
    expect(page.get_by_test_id('trace-waterfall')).to_be_visible()
    expect(page.get_by_test_id('live-service-map').locator('.react-flow__edge')).to_have_count(3)
    assert page.get_by_test_id('live-service-map').locator('.react-flow__edge.animated').count()>=2
    expect(page.get_by_text('SELECT payment',exact=True)).to_be_visible()
    page.screenshot(path=f'/tmp/sentinelops-restored-traces-{width}.png',full_page=True)
    page.get_by_role('tab',name=re.compile('^Metrics')).click()
    expect(page.locator('.recharts-area').first).to_be_visible()
    nav('Incidents')
    rows = page.get_by_test_id('live-incident-row')
    expect(rows).to_have_count(len(records) - 1)
    expect(rows.filter(has_text=incoming)).to_have_count(0)
    incoming_visible = True
    page.get_by_role('button', name='Refresh live data', exact=True).click()
    check_incident_list(page, records, width)
    incoming_badge = rows.filter(has_text=incoming).get_by_test_id('rca-confidence')
    expect(incoming_badge).to_have_attribute('data-source', 'ai')
    expect(incoming_badge).to_contain_text('73%')
    assert_provider_hidden(page, 'incident list (EN)')
    set_locale(page, 'vi')
    assert_provider_hidden(page, 'incident list (VI)')
    set_locale(page, 'en')
    nav('Dashboard')
    expect(page.get_by_role('heading', name='Core Services Health', exact=True)).to_be_visible()
    expect(incoming_badge).to_be_visible()
    expect(incoming_badge).to_have_attribute('data-source', 'ai')
    assert_provider_hidden(page, 'dashboard (EN)')
    set_locale(page, 'vi')
    assert_provider_hidden(page, 'dashboard (VI)')
    set_locale(page, 'en')
    nav('Incidents')
    assert 'code=' not in page.url and 'state=' not in page.url
    assert page.evaluate('sessionStorage.getItem("sentinelops.pkce.v1")') is None
    assert fixture['access'] not in page.evaluate('JSON.stringify({...localStorage,...sessionStorage})')
    assert not page.locator('text=INC-2026-1048').count()
    for incident, mode, source in EXPLANATION_CASES:
        page.get_by_test_id('live-incident-row').filter(has_text=incident).click()
        expect(page.get_by_test_id('live-incident-id')).to_contain_text(incident)
        check_explanation_panel(page, explanations[incident, revisions[incident]], mode, source, width, output_dir)
        if incident != EXPLANATION_CASES[-1][0]:
            nav('Incidents')
    nav('Remediation')
    card=page.get_by_test_id('live-proposal')
    expect(card).to_be_visible()
    card.get_by_role('textbox',name='Decision reason',exact=True).fill('Reviewed both immutable image digests')
    approve=card.get_by_role('button',name='Approve',exact=True)
    if read_only:
        expect(approve).to_be_disabled()
        expect(card.get_by_role('button',name='Reject',exact=True)).to_be_disabled()
    else:
        expect(approve).to_be_enabled()
        page.once('dialog',lambda d:d.dismiss());approve.click();assert not fixture['posts']
        fixture['missing']=True
        page.get_by_role('button',name='Reload proposals',exact=True).click()
        expect(card.get_by_text('Both pinned image digests are required before approval.',exact=True)).to_be_visible()
        card.get_by_role('textbox',name='Decision reason',exact=True).fill('Reviewed incomplete image evidence')
        expect(approve).to_be_disabled();expect(card.get_by_role('button',name='Reject',exact=True)).to_be_enabled()
        fixture['missing']=False;fixture['stale']=True
        page.get_by_role('button',name='Reload proposals',exact=True).click()
        expect(card.get_by_text('Incident revision advanced. Reload the current incident.',exact=True)).to_be_visible()
        assert approve.count()==0, 'Stale proposals must have no decision form'
        fixture['stale']=False
        page.get_by_role('button',name='Reload proposals',exact=True).click()
        expect(card.get_by_text('pending',exact=True)).to_be_visible()
        card.get_by_role('textbox',name='Decision reason',exact=True).fill('Fresh revision and image review')
        expect(approve).to_be_enabled()
        fixture['lose_reply']=True
        page.once('dialog',lambda d:d.accept());approve.click()
        expect(page.get_by_role('alert').filter(has_text='Reload to verify')).to_be_visible()
        assert len(fixture['posts'])==1 and page.get_by_test_id('live-proposal').count()==0
        fixture['lose_reply']=False
        page.get_by_role('button',name='Reload proposals',exact=True).click()
        card.get_by_role('textbox',name='Decision reason',exact=True).fill('Explicit second review after reloading')
        page.once('dialog',lambda d:d.accept());card.get_by_role('button',name='Approve',exact=True).click()
        expect(card.get_by_text('fixture-server-actor',exact=False)).to_be_visible()
        assert len(fixture['posts'])==2
    # Inspect evidence, error state, locale and responsive layout with real shapes.
    nav('Evidence & Trace')
    assert page.locator('img[src="x"]').count()==0 and not page.evaluate('window.__evidence_xss || false')
    nav('Remediation')
    expect(page.get_by_role('alert').filter(has_text='Action history is unavailable')).to_be_visible()
    page.get_by_role('button',name=re.compile('^vi$',re.I)).click()
    expect(page.get_by_role('button',name='Ngắt kết nối',exact=True)).to_be_visible()
    assert_no_horizontal_overflow(page)
    page.screenshot(path=f'/tmp/sentinelops-live-{width}.png',full_page=True)
    page.get_by_role('button',name='Ngắt kết nối',exact=True).click()
    expect(page.get_by_role('button',name='Đăng nhập Cognito',exact=True)).to_be_visible()
    assert not page.get_by_test_id('live-incident-row').count() and not page.get_by_test_id('live-proposal').count()
    assert not errors,errors
    context.close()
    return {'viewport':width,'read_only':read_only,'signed_oidc_callback':True,'pkce_survived_redirect':True,
            'api_incidents':len(records),'decision_posts':len(fixture['posts']),'mobile_overflow':False,
            'explanation_states':[source for _, _, source in EXPLANATION_CASES],
            'provider_hidden':True,'structured_rca_narrative':True,
            'newest_incident_first':True,'incident_display_timezone':'Asia/Ho_Chi_Minh'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / '.herdr/out', help='Directory for RCA panel screenshots')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    widths = [int(width) for width in os.environ.get('UI_TEST_WIDTHS', '1440,390').split(',')]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path='/run/current-system/sw/bin/google-chrome-stable', headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage'],
        )
        try:
            scenarios = [scenario(browser, width, args.output_dir, width < 1024) for width in widths]
            print(json.dumps({'status':'PASS','scenarios':scenarios}))
        finally:
            browser.close()


if __name__ == '__main__':
    main()
