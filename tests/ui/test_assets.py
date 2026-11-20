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


def test_proposals_render_static_expiry_without_countdown_assets():
    app = (routes.ASSETS/'app.js').read_text()
    watch = (routes.ASSETS/'proposalwatch.js').read_text()
    assert all('`Expires ${expiryTime(' in source for source in (app, watch))
    removed = ('proposal-countdown','proposalwatch-countdown','Review window estimate',
               'Proposal TTL:','s remaining','left to approve','Urgent','Ending soon')
    assert all(text not in app+watch for text in removed)
    assert 'Decision recorded' in app and 'Decision recorded' in watch
    assert '.proposal-countdown' not in (routes.ASSETS/'styles.css').read_text()
    assert '.proposalwatch-countdown' not in (routes.ASSETS/'proposalwatch.css').read_text()


STATUS_COLORS = ('good','warning','serious','critical')
TEXT_BACKGROUNDS = (
    'page','surface','subtle','good-tint','warning-tint','serious-tint',
    'critical-tint','blue-tint',
)


@pytest.fixture
def white_theme_colors():
    stylesheet = (routes.ASSETS/'styles.css').read_text()
    colors = dict(re.findall(r'--([a-z-]+):\s*(#[0-9a-fA-F]{6});',stylesheet))
    aliases = dict(re.findall(r'--([a-z-]+):\s*var\(--([a-z-]+)\);',stylesheet))
    for alias in aliases:
        token = alias
        seen = set()
        while token in aliases:
            assert token not in seen, f'Circular color alias: {alias}'
            seen.add(token)
            token = aliases[token]
        assert token in colors, f'Unknown color token: {token}'
        colors[alias] = colors[token]
    return colors


def contrast_ratio(foreground, background):
    def luminance(color):
        channels = [int(color[offset:offset+2],16)/255 for offset in (1,3,5)]
        linear = [channel/12.92 if channel <= .04045 else ((channel+.055)/1.055)**2.4
                  for channel in channels]
        return sum(channel*weight for channel,weight in zip(linear,(.2126,.7152,.0722)))

    light, dark = sorted((luminance(foreground),luminance(background)),reverse=True)
    return (light+.05)/(dark+.05)


def test_white_theme_text_pairs_meet_wcag_aa(white_theme_colors):
    colors = white_theme_colors
    assert colors['page'] == colors['surface'] == '#ffffff'
    # Status tokens color borders/icon backgrounds, always with an icon and label.
    # Their low contrast on white is intentional: do not add them back as text.
    # Guard their CSS usage and test text ON status backgrounds separately below.
    for foreground in ('ink','secondary','muted'):
        for background in TEXT_BACKGROUNDS:
            ratio = contrast_ratio(colors[foreground],colors[background])
            assert ratio >= 4.5, f'{foreground} on {background}: {ratio:.4f}:1 < 4.5:1'


def test_white_theme_blue_text_pairs_meet_wcag_aa(white_theme_colors):
    # Blue remains a text color in links and active navigation.
    for background in TEXT_BACKGROUNDS:
        ratio = contrast_ratio(white_theme_colors['blue'],white_theme_colors[background])
        assert ratio >= 4.5, f'blue on {background}: {ratio:.4f}:1 < 4.5:1'


def test_status_colors_are_never_used_as_text_color():
    stylesheets = {
        path: re.sub(r'/\*.*?\*/',lambda m: '\n'*m.group().count('\n'),path.read_text(),flags=re.S)
        for path in sorted(routes.ASSETS.rglob('*.css'))
    }
    assert stylesheets, 'No CSS assets found'
    variable = re.compile(r'var\(\s*(--[\w-]+)',re.I)
    declaration = re.compile(r'(?:^|[;{}])\s*(--[\w-]+)\s*:\s*([^;{}]+)')
    text_color = re.compile(r'(?:^|[;{}])\s*color\s*:\s*([^;{}]+)',re.I)
    status_tokens = {'--'+name for name in STATUS_COLORS}
    aliases = [match.groups() for css in stylesheets.values() for match in declaration.finditer(css)]
    # Follow aliases too: color: var(--status-color) or var(--danger) is unsafe.
    while True:
        derived = {name for name,value in aliases if status_tokens.intersection(variable.findall(value))}
        if derived <= status_tokens:
            break
        status_tokens.update(derived)
    violations = []
    for path,css in stylesheets.items():
        for match in text_color.finditer(css):
            if status_tokens.intersection(variable.findall(match.group(1))):
                line = css.count('\n',0,match.start(1))+1
                violations.append(f'{path}:{line}: color: {match.group(1).strip()}')
    assert not violations, 'Status colors must not color text:\n'+'\n'.join(violations)


@pytest.mark.parametrize('status',STATUS_COLORS)
def test_status_icon_text_pairs_meet_wcag_aa(white_theme_colors, status):
    stylesheet = re.sub(r'/\*.*?\*/','',(routes.ASSETS/'styles.css').read_text(),flags=re.S)
    icon = re.search(r'(?:^|})\s*\.status-chip__icon\s*\{([^}]*)}',stylesheet)
    assert icon, 'Missing status chip icon rule'
    assert re.search(r'(?:^|;)\s*background\s*:\s*var\(--status-color\)\s*(?:;|$)',icon.group(1))
    assert re.search(r'(?:^|;)\s*color\s*:\s*var\(--text\)\s*(?:;|$)',icon.group(1))
    assert re.search(
        rf'\.status-chip\[data-status=["\']?{status}["\']?\]\s*\{{\s*'
        rf'--status-color\s*:\s*var\(--{status}\)\s*;?\s*}}',stylesheet,
    ), f'Missing status background mapping: {status}'
    # Keep an explicit expected palette, backed by assertions on the real CSS.
    # Apply status-specific overrides before checking the table so changing or
    # removing critical's inverse text cannot silently leave a stale color pair.
    expected_text = {
        'good': 'text', 'warning': 'text', 'serious': 'text',
        'critical': 'text-inverse',
    }[status]
    color_declaration = re.compile(r'(?:^|;)\s*color\s*:\s*([^;]+)',re.I)
    actual_color = color_declaration.findall(icon.group(1))[-1].strip()
    overrides = re.findall(
        rf'\.status-chip\[data-status=["\']?{status}["\']?\]\s*>\s*'
        r'\.status-chip__icon\s*\{([^}]*)}',stylesheet,
    )
    for override in overrides:
        declarations = color_declaration.findall(override)
        if declarations:
            actual_color = declarations[-1].strip()
    assert re.fullmatch(rf'var\(\s*--{expected_text}\s*\)',actual_color), (
        f'{status} icon CSS uses {actual_color}, expected var(--{expected_text})'
    )
    # These 10-12px glyphs do not qualify as large text: keep AA at 4.5:1.
    ratio = contrast_ratio(white_theme_colors[expected_text],white_theme_colors[status])
    assert ratio >= 4.5, (
        f'--{expected_text} on --{status}: {ratio:.4f}:1 < 4.5:1'
    )
