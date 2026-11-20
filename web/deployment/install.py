"""Register the demo routes in the existing operator UI image."""

from pathlib import Path

package = Path('/opt/venv/lib/python3.11/site-packages/sentinelops')
api = package / 'api.py'
routes = package / 'ui' / 'routes.py'
original = "        if request.url.path.startswith('/ui') and settings.oidc and settings.oidc.login_domain:"
replacement = """        is_demo = request.url.path == '/ui/demo' or request.url.path.startswith('/ui/demo/')
        if request.url.path.startswith('/ui') and not is_demo and settings.oidc and settings.oidc.login_domain:"""
source = api.read_text()
if source.count(original) != 1:
    raise RuntimeError('The operator UI CSP middleware differs from the expected base image')
api.write_text(source.replace(original, replacement))
with routes.open('a') as handle:
    handle.write('\nfrom .demo_routes import router as demo_router\nrouter.include_router(demo_router)\n')
