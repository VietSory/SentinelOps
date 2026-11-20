"""Serve the static demo within the existing operator UI edge route."""

from pathlib import Path
import re

from fastapi import APIRouter, HTTPException
from starlette.responses import FileResponse, RedirectResponse

router = APIRouter(include_in_schema=False)
SITE = Path(__file__).resolve().parent / 'demo_site'
ASSET_NAME = re.compile(r'[A-Za-z0-9_-]+\.(?:css|js)')
HEADERS = {
    'Content-Security-Policy': (
        "default-src 'none'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'none'"
    ),
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'X-Frame-Options': 'DENY',
}


@router.api_route('/ui/demo', methods=['GET', 'HEAD'])
async def demo_redirect():
    return RedirectResponse('/ui/demo/', headers=HEADERS)


@router.api_route('/ui/demo/', methods=['GET', 'HEAD'])
async def demo_index():
    return FileResponse(SITE / 'index.html', headers=HEADERS)


@router.api_route('/ui/demo/assets/{name}', methods=['GET', 'HEAD'])
async def demo_asset(name: str):
    if not ASSET_NAME.fullmatch(name):
        raise HTTPException(404, 'Demo asset not found')
    path = SITE / 'assets' / name
    if not path.is_file():
        raise HTTPException(404, 'Demo asset not found')
    media_type = 'text/css' if name.endswith('.css') else 'text/javascript'
    return FileResponse(path, headers=HEADERS, media_type=media_type)
