"""Serve the real operator UI with shared software fixtures on IPv4 loopback."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from scripts.check_operator_ui import (
    FIXTURE_TOKEN, fixture_actions, fixture_envelope, fixture_explanation,
    fixture_proposals, fixture_quarantine, fixture_records,
)
from sentinelops.ui.routes import HEADERS, router

WARNING = ('DỮ LIỆU GIẢ / SYNTHETIC FIXTURES — chỉ để duyệt giao diện LOCAL. '
           'KHÔNG phải dữ liệu thật; KHÔNG dùng để kết luận về hệ thống thật.')
PREVIEW_META = ('<meta name="sentinelops-grafana-url" content="/preview/grafana/">'
                '<meta name="sentinelops-jaeger-url" content="/preview/jaeger/">')
PREVIEW_BANNER = (f'<aside role="note" class="callout uncertain" id="fixture-warning">'
                  f'<strong>{WARNING}</strong><span>Chỉ dùng token giả ở trang '
                  '<a href="/">Hướng dẫn preview</a></span></aside>')


def create_preview_app(*, now=None):
    # One clock per process, never a moving expiry renewed by the watch's poll.
    now = now or datetime.now(timezone.utc)
    records = fixture_records(now)
    proposals = fixture_proposals(now)
    app = FastAPI(title='SentinelOps SYNTHETIC LOCAL PREVIEW',
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)

    @app.middleware('http')
    async def preview_boundary(request: Request, call_next):
        if request.client is None or request.client.host != '127.0.0.1':
            return JSONResponse({'code':'fixture_loopback_only','message':WARNING}, status_code=403)
        if request.method not in ('GET', 'HEAD'):
            return JSONResponse({'code':'fixture_read_only','message':WARNING}, status_code=405,
                                headers={'Allow':'GET, HEAD','Cache-Control':'no-store',
                                         'X-SentinelOps-Data':'synthetic-fixture-only'})
        response = await call_next(request)
        if request.url.path == '/ui/' and response.status_code == 200:
            # Keep routes.py as the source of HTML/CSP. Decorate only this local
            # response; never copy or edit the shipped index, JS, CSS or router.
            del response.headers['etag']
            del response.headers['last-modified']
            if request.method == 'GET':
                body = b''.join([chunk async for chunk in response.body_iterator]).decode('utf-8')
                body = body.replace('</head>', PREVIEW_META+'</head>', 1)
                body = body.replace('<body>', '<body>'+PREVIEW_BANNER, 1)
                del response.headers['content-length']
                response = HTMLResponse(body, headers=dict(response.headers))
            else:
                del response.headers['content-length']
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-SentinelOps-Data'] = 'synthetic-fixture-only'
        return response

    @app.exception_handler(HTTPException)
    async def error_response(request, error):
        return JSONResponse({'code':error.detail,'message':WARNING}, status_code=error.status_code)

    async def preview_reader(authorization: str | None = Header(None)):
        if authorization != 'Bearer '+FIXTURE_TOKEN:
            raise HTTPException(401, 'unauthorized')

    def selected(incident_id, revision):
        record = next((row for row in records if row['incident_id'] == incident_id), None)
        if record is None or revision is not None and revision > record['revision']:
            raise HTTPException(404, 'incident_not_found')
        return revision or record['revision']

    @app.api_route('/', methods=['GET', 'HEAD'])
    async def instructions():
        return HTMLResponse(f'<!doctype html><html lang="vi"><meta charset="utf-8">'
            f'<title>SentinelOps — DỮ LIỆU GIẢ</title><h1>{WARNING}</h1>'
            '<p>Không kết nối database, RDS, Kubernetes, model hay tài khoản production.</p>'
            f'<p>Mở <a href="/ui/">operator UI</a>, nhập token giả <code>{FIXTURE_TOKEN}</code>, '
            'rồi bấm Connect. Không nhập token thật.</p>'
            '<p>8 sự cố; bộ lọc 24 giờ loại 1 sự cố cũ. Chọn INC-FIXTURE-A để xem proposal pending, '
            'explanation, actions và quarantine. Mọi thao tác ghi đều bị chặn.</p>'
            '<p>Đếm ngược chạy từ lúc server khởi động, không tự gia hạn khi poll. '
            'Khởi động lại server để bắt đầu lượt duyệt mới. '
            'Các link Grafana/Jaeger dẫn đến trang fixture local, không có telemetry thật.</p></html>',
            headers=HEADERS)

    @app.get('/ui/auth-config')
    async def auth_config():
        return {'mode':'manual'}

    @app.get('/ready')
    async def ready():
        return {'status':'ready','dependencies':{'incident_store':True,'model':True,'authentication':True},
                'demo_mode':True,'direct_inference':False,'fixture_mode':True,'warning':WARNING}

    @app.get('/ready/incidents', dependencies=[Depends(preview_reader)])
    async def incidents_ready():
        return {'status':'ready','component':'incident-store','fixture_mode':True}

    @app.get('/v1/incidents', dependencies=[Depends(preview_reader)])
    async def incidents(state: str | None = None, service: str | None = None,
                        cursor: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)):
        rows = [row for row in records if (not state or row['state'] == state) and
                (not service or service in row['affected_services'])]
        end = cursor+limit
        return {'schema_version':'1.0','items':rows[cursor:end],
                'next_cursor':str(end) if end < len(rows) else None}

    @app.get('/v1/incidents/{incident_id}/context', dependencies=[Depends(preview_reader)])
    async def context(incident_id: str, revision: int | None = Query(None, ge=1),
                      after_revision: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        revision = selected(incident_id, revision)
        # Retain the shared fixture's first-page behavior; honor smaller limits.
        result = fixture_envelope(incident_id, revision, after_revision, now=now)
        events = result['timeline']['events'][:limit]
        result['timeline'] = {'events':events,'next_after_revision':
            events[-1]['revision'] if events and events[-1]['revision'] < revision else None}
        return result

    @app.get('/v1/incidents/{incident_id}/explanation', dependencies=[Depends(preview_reader)])
    async def explanation(incident_id: str, response: Response, revision: int = Query(..., ge=1)):
        selected(incident_id, revision)
        response.headers['X-Incident-Revision'] = str(revision)
        return fixture_explanation(incident_id, revision, now=now)

    @app.get('/v1/incidents/{incident_id}/actions', dependencies=[Depends(preview_reader)])
    async def actions(incident_id: str, revision: int = Query(..., ge=1),
                      cursor: Literal['fixture-actions-2'] | None = None,
                      limit: int = Query(10, ge=1, le=25)):
        selected(incident_id, revision)
        return fixture_actions(incident_id, revision, cursor, now=now)

    @app.get('/v1/actions/{action_id}/quarantine', dependencies=[Depends(preview_reader)])
    async def quarantine(action_id: str):
        if action_id not in ('1'*64, '2'*64):
            raise HTTPException(404, 'action_not_found')
        return fixture_quarantine(action_id, now=now)

    @app.get('/v1/runbook-proposals', dependencies=[Depends(preview_reader)])
    async def runbook_proposals(incident_id: str | None = None, limit: int = Query(50, ge=1, le=100)):
        rows = [row for row in proposals if incident_id is None or row['proposal']['incident_id'] == incident_id]
        expired = datetime.now(timezone.utc) >= datetime.fromisoformat(proposals[0]['proposal']['expires_at'])
        return {'items':[{**row,'status':'expired' if expired else 'pending'} for row in rows[:limit]],
                'has_more':len(rows) > limit}

    @app.get('/preview/{service}/{path:path}')
    async def telemetry_placeholder(service: Literal['grafana', 'jaeger'], path: str):
        return HTMLResponse(f'<html lang="vi"><meta charset="utf-8"><h1>{WARNING}</h1>'
            f'<p>Đích liên kết {service} fixture local. Không có dashboard hay trace thật.</p>'
            '<a href="/ui/">Về operator UI</a></html>', headers=HEADERS)

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18080)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).warning('%s\nhttp://127.0.0.1:%s/\nToken GIẢ: %s',
                                        WARNING, args.port, FIXTURE_TOKEN)
    uvicorn.run(create_preview_app(), host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
