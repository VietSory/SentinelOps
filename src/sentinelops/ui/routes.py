from pathlib import Path

from fastapi import APIRouter, HTTPException
from starlette.responses import FileResponse, RedirectResponse

router = APIRouter(include_in_schema=False)
ASSETS = Path(__file__).resolve().parent/'assets'
HEADERS = {
    'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY',
}


@router.get('/ui')
async def ui_redirect():
    return RedirectResponse('/ui/',headers=HEADERS)


@router.api_route('/ui/',methods=['GET','HEAD'])
async def ui_index():
    return FileResponse(ASSETS/'index.html',headers=HEADERS)


@router.api_route('/ui/assets/{name}',methods=['GET','HEAD'])
async def ui_asset(name: str):
    if name not in {'app.js','client.js','workspace.js','styles.css'}:
        raise HTTPException(404,'UI asset not found')
    return FileResponse(ASSETS/name,headers=HEADERS)
