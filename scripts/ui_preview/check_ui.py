"""Browser smoke against the running fixture HTTP server, without interception."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

import httpx

from scripts.check_operator_ui import FIXTURE_TOKEN, verify_ui_assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:18080')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--browser-executable', default=os.getenv('SENTINELOPS_CHROME'))
    args = parser.parse_args()
    origin = args.base_url.rstrip('/')
    url = urlsplit(origin)
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or
        url.path or url.username is not None or url.query or url.fragment):
        parser.error('--base-url requires an explicit http://127.0.0.1:port origin')
    with httpx.Client(base_url=origin, trust_env=False) as client:
        assert client.get('/ready').json()['fixture_mode'] is True
        assets = verify_ui_assets(client)
    args.output.mkdir(parents=True, exist_ok=False)
    if shutil.which('node'):
        os.environ.setdefault('PLAYWRIGHT_NODEJS_PATH', shutil.which('node'))
    from playwright.sync_api import expect, sync_playwright
    errors, requests = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser_executable, headless=True,
                                              args=['--no-sandbox','--disable-dev-shm-usage'])
        try:
            page = browser.new_page(viewport={'width':1440,'height':1100})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append((request.method, request.url)))
            page.goto(origin+'/ui/', wait_until='networkidle')
            expect(page.locator('#fixture-warning')).to_contain_text('DỮ LIỆU GIẢ')
            page.locator('#api-token').fill(FIXTURE_TOKEN)
            page.get_by_role('button', name='Connect', exact=True).click()
            rows = page.get_by_test_id('incident-row')
            expect(rows).to_have_count(8)
            expect(page.locator('#store-status')).to_have_text('Available')
            expect(page.locator('#model-status')).to_have_text('Available')
            expect(page.locator('.incident-summary__card--loaded dd')).to_have_text('8')
            expect(page.locator('.incident-summary__metric--critical dd')).to_have_text('2')
            expect(page.locator('.incident-summary__metric--high dd')).to_have_text('3')
            expect(page.locator('.incident-summary__metric--recovered dd')).to_have_text('2')
            page.get_by_role('combobox', name='Severity', exact=True).select_option('critical')
            expect(rows).to_have_count(2)
            page.get_by_role('combobox', name='Severity', exact=True).select_option('')
            page.get_by_role('combobox', name='Detected within').select_option('24h')
            expect(rows).to_have_count(7)
            page.get_by_role('combobox', name='Detected within').select_option('all')
            expect(rows).to_have_count(8)
            page.locator('#state-filter').select_option('recovered')
            page.get_by_role('button', name='Apply filters', exact=True).click()
            expect(rows).to_have_count(2)
            page.locator('#state-filter').select_option('')
            page.get_by_role('button', name='Apply filters', exact=True).click()
            expect(rows).to_have_count(8)
            page.get_by_role('button', name='Open INC-FIXTURE-A, revision 3', exact=True).click()
            expect(page.locator('#detail-title')).to_have_text('INC-FIXTURE-A')
            links = page.locator('#slot-deeplinks a')
            expect(links).to_have_count(3)
            for href in links.evaluate_all('(nodes) => nodes.map(node => node.href)'):
                assert href.startswith(origin+'/preview/')
                with httpx.Client(trust_env=False) as client:
                    assert 'DỮ LIỆU GIẢ' in client.get(href).text
            watch = page.locator('#slot-proposalwatch')
            expect(watch.locator('[data-status=pending]')).to_have_count(1)
            countdown = watch.locator('.proposalwatch-countdown')
            expect(countdown).to_contain_text('Review window estimate:')
            first = int(re.search(r'(\d+)s remaining', countdown.inner_text()).group(1))
            page.wait_for_function('before => { const text = document.querySelector(".proposalwatch-countdown")?.textContent; '
                r'const match = text?.match(/(\d+)s remaining/); return match && Number(match[1]) < before; }', arg=first)
            before_poll = watch.locator('.proposalwatch-meta').all_text_contents()
            watch.get_by_role('button', name='Refresh now', exact=True).click()
            expect(watch.locator('[data-status=pending]')).to_have_count(1)
            expect(page.get_by_test_id('runbook-proposals')).to_contain_text('awaiting decision')
            page.screenshot(path=str(args.output/'fixture-preview-desktop.png'), full_page=True)
            page.get_by_role('button', name='Explanation', exact=True).click()
            expect(page.locator('.explanation-summary')).to_contain_text('Software fixture')
            page.get_by_role('button', name='Actions', exact=True).click()
            expect(page.locator('.evidence-card')).to_have_count(1)
            page.locator('.evidence-card summary').click()
            page.get_by_role('button', name='View quarantine status', exact=True).click()
            expect(page.get_by_text('Quarantine released by operator', exact=True)).to_be_visible()
            assert not page.locator('#detail-pane img').count()
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            expect(page.locator('#fixture-warning')).to_contain_text('DỮ LIỆU GIẢ')
            page.screenshot(path=str(args.output/'fixture-preview-mobile.png'), full_page=True)
            assert not errors, errors
            assert all(method == 'GET' and address.startswith(origin+'/') for method, address in requests), requests
            result = {'status':'PASS','scope':'synthetic-local-UI-only','origin':origin,
                'modules':{'filters':'8 → critical 2 / recovered 2 / 24h 7',
                           'summary':'8 total, critical 2, high 3, recovered 2',
                           'deeplinks':'3 local fixture links', 'proposalwatch':'pending, countdown decreases'},
                'countdown_initial_seconds':first,'route_interception':False,'page_errors':errors,
                'assets':assets,'requests':len(requests),'poll_metadata':before_poll}
            (args.output/'verification.json').write_text(json.dumps(result, indent=2)+'\n')
            print(json.dumps(result), flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
