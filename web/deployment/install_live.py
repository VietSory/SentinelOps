"""Install the live shell while retaining the vetted API/auth client modules."""
from pathlib import Path
import re

package = Path('/opt/venv/lib/python3.11/site-packages/sentinelops')
routes = package / 'ui/routes.py'
source = routes.read_text()
assert source.count('HEADERS = {') == 1
addition = """ASSET_NAMES = ASSET_NAMES | frozenset(p.name for p in ASSETS.iterdir()
    if p.is_file() and __import__('re').fullmatch(r'[A-Za-z0-9_-]+\\.(?:js|css)', p.name))
"""
source = source.replace('HEADERS = {', addition + '\nHEADERS = {', 1)
# React/Tailwind components use inline style attributes. Scripts remain self-only.
source = source.replace("style-src 'self';", "style-src 'self' 'unsafe-inline';")
routes.write_text(source)

api = package / 'api.py'
code = api.read_text()
assert 'UI_HEADERS' in code
# Existing middleware still limits connect-src to self and the two Cognito origins.
assert 'settings.oidc.login_domain' in code
if 'install_live_observability' not in code:
    anchor='    install_proposal_routes(app, settings,'
    assert code.count(anchor)==1
    code=code.replace(anchor,
        '    from sentinelops.ui.live_observability import install_routes as install_live_observability\n'
        '    install_live_observability(app, authorize_operator, settings.environment)\n\n'+anchor,1)
    api.write_text(code)

html = package / 'ui/assets/index.html'
assert html.is_file() and '/ui/assets/' in html.read_text()
assert re.search(r'<script[^>]+src="/ui/assets/[A-Za-z0-9_-]+\.js"', html.read_text())
