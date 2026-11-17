"""Local synthetic workspace and a non-persistent adapter for the pinned hospital UI."""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from team_chat_gateway import _base_url

ROOT = Path(__file__).resolve().parent
TEAM = ROOT / '.local/team-source/Quick119-37492e19240490d656e7f4f85cb43f52e986bd0b/hospital-ui'
templates = Jinja2Templates(directory=ROOT / 'templates')
router = APIRouter()


def chat_ui_enabled():
    return os.getenv('QUICK119_TEAM_CHAT_ENABLED') == '1' and os.getenv('QUICK119_ENV', 'development') in {'development', 'test'}


def local_only(request):
    _base_url()
    if request.client is None or request.client.host not in {'127.0.0.1', '::1'}:
        raise HTTPException(403, '가상 데이터 UI는 로컬 PC에서만 사용할 수 있습니다.')


@router.get('/transport/', include_in_schema=False)
def workspace(request: Request):
    local_only(request)
    return templates.TemplateResponse(request=request, name='transport.html', context={
        'active_nav': 'transport', 'app_env': 'test', 'chat_ui_enabled': True})


@router.get('/hospital/', include_in_schema=False)
@router.get('/hospital/{asset}', include_in_schema=False)
def hospital(request: Request, asset: str = 'index.html'):
    local_only(request)
    if asset not in {'index.html', 'chat-recovery.mjs', 'inquiry-alerts.mjs', 'inquiry-layout.css',
                     'operations.mjs', 'review-discovery.mjs', 'workflow-panel.css',
                     'workflow-panel.mjs', 'workflow-state.mjs'} or not (TEAM / asset).is_file():
        raise HTTPException(404)
    # Keep the pinned source untouched. Every original storage reference uses a
    # per-page RAM adapter, including ChatRecoveryStore's read-back verification.
    text = (TEAM / asset).read_text(encoding='utf-8').replace('localStorage', 'quick119MemoryStorage')
    if asset == 'index.html':
        text = text.replace('<head>', '<head><script src="/static/hospital-memory.js"></script>')
        text = text.replace("params.get('server') ||", '')
        text = text.replace("serverBase = $('server').value.trim().replace(/\\/$/, '');", "serverBase = location.origin;")
        text = text.replace("$('server').value = serverBase;", "$('server').value = serverBase; $('server').readOnly = true;")
        text = text.replace('`${serverBase}/patient/?pid=', '`${serverBase}/transport/?pid=')
        text = text.replace('</head>', '<link rel="stylesheet" href="/static/hospital-adapter.css"></head>')
        text = text.replace('<body>', '<body><aside class="synthetic-hospital-note">가상 병원 · 수용 문의와 이송 대화 검증용입니다. 초안·전송 복구는 현재 페이지 메모리에만 보관되며 새로고침 시 소실됩니다.</aside>')
    media = 'text/html' if asset.endswith('.html') else 'text/css' if asset.endswith('.css') else 'text/javascript'
    return Response(text, media_type=media, headers={'Cache-Control': 'no-store',
        'Content-Security-Policy': "connect-src 'self'; object-src 'none'; base-uri 'self'"})
