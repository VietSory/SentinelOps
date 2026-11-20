from html.parser import HTMLParser
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from scripts.check_operator_ui import UI_ASSETS, UI_CSP, verify_ui_assets
from sentinelops.ui import routes


@pytest.fixture
def asset_client(tmp_path, monkeypatch):
    for name in UI_ASSETS:
        source = routes.ASSETS/name
        content = source.read_bytes() if source.is_file() else b''
        (tmp_path/name).write_bytes(content)
    (tmp_path/'index.html').write_bytes((routes.ASSETS/'index.html').read_bytes())
    (tmp_path/'private.js').write_text('private asset')
    (tmp_path/'private.css').write_text('private asset')
    monkeypatch.setattr(routes,'ASSETS',tmp_path)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        yield client


def test_allowlisted_assets_have_correct_mime_and_unchanged_csp(asset_client):
    assert routes.ASSET_NAMES == set(UI_ASSETS)
    assert verify_ui_assets(asset_client) == {
        'assets_checked':12,'asset_methods':['GET','HEAD'],'strict_csp':True,
    }
    redirect = asset_client.get('/ui',follow_redirects=False)
    assert redirect.headers['content-security-policy'] == UI_CSP
    assert redirect.headers['location'] == '/ui/'


@pytest.mark.parametrize('method',['get','head'])
def test_missing_allowlisted_asset_returns_404(asset_client, method):
    (routes.ASSETS/'summary.js').unlink()
    assert getattr(asset_client,method)('/ui/assets/summary.js').status_code == 404


class ShellParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag,dict(attrs)))


def test_shell_loads_external_modules_and_persistent_slots():
    parser = ShellParser()
    parser.feed((routes.ASSETS/'index.html').read_text())
    ids = [attrs['id'] for _,attrs in parser.tags if 'id' in attrs]
    assert len(ids) == len(set(ids))
    for module in ('deeplinks','filters','summary','proposalwatch'):
        assert 'slot-'+module in ids
        assert not any(tag == 'script' and attrs.get('src') == f'/ui/assets/{module}.js'
                       for tag,attrs in parser.tags)
        assert any(tag == 'link' and attrs.get('rel') == 'stylesheet' and
                   attrs.get('href') == f'/ui/assets/{module}.css' for tag,attrs in parser.tags)
    assert all('src' in attrs for tag,attrs in parser.tags if tag == 'script')
    assert all(tag != 'style' and 'style' not in attrs and
               not any(name.startswith('on') for name in attrs) for tag,attrs in parser.tags)


def test_white_theme_text_pairs_meet_wcag_aa():
    stylesheet = (routes.ASSETS/'styles.css').read_text()
    colors = dict(re.findall(r'--([a-z-]+):\s*(#[0-9a-fA-F]{6});',stylesheet))

    def luminance(color):
        channels = [int(color[offset:offset+2],16)/255 for offset in (1,3,5)]
        linear = [channel/12.92 if channel <= .04045 else ((channel+.055)/1.055)**2.4
                  for channel in channels]
        return sum(channel*weight for channel,weight in zip(linear,(.2126,.7152,.0722)))

    assert colors['page'] == colors['surface'] == '#ffffff'
    for foreground in ('ink','secondary','muted','good','warning','critical','blue'):
        for background in ('page','surface','subtle','good-tint','warning-tint','critical-tint','blue-tint'):
            light, dark = sorted((luminance(colors[foreground]),luminance(colors[background])),reverse=True)
            assert (light+.05)/(dark+.05) >= 4.5, (foreground,background)
