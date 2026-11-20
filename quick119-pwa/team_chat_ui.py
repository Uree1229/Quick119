"""Local synthetic workspace and a non-persistent adapter for the repository's hospital UI."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

import tts_audio
from team_chat_gateway import _base_url

ROOT = Path(__file__).resolve().parent
HOSPITAL_UI = ROOT.parent / 'hospital-ui'
templates = Jinja2Templates(directory=ROOT / 'templates')
router = APIRouter()


def chat_ui_enabled(request: Request) -> bool:
    try:
        local_only(request)
    except HTTPException:
        return False
    return True


def local_only(request):
    _base_url()
    if request.client is None or request.client.host not in {'127.0.0.1', '::1'}:
        raise HTTPException(403, '가상 데이터 UI는 로컬 PC에서만 사용할 수 있습니다.')


@router.get('/transport/', include_in_schema=False)
def workspace(request: Request):
    local_only(request)
    return templates.TemplateResponse(request=request, name='transport.html', context={
        'active_nav': 'transport', 'app_env': 'test', 'chat_ui_enabled': True,
        'tts_cloud': tts_audio.neural_enabled()})


@router.post('/api/transport-tts', include_in_schema=False)
async def transport_speech(request: Request):
    """Render a system message as Korean audio for the transport chat."""
    local_only(request)
    origin = request.headers.get('origin')
    if (request.headers.get('sec-fetch-site') == 'cross-site'
            or (origin and origin != f'{request.url.scheme}://{request.url.netloc}')):
        raise HTTPException(403, '다른 출처의 낭독 요청은 허용하지 않습니다.')
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 4096:
            raise HTTPException(413, '낭독할 문장이 너무 깁니다.')
        body.extend(chunk)
    try:
        payload = json.loads(body)
        text = payload.get('text') if isinstance(payload, dict) else None
        audio, media_type = await run_in_threadpool(tts_audio.synthesize, text)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(400, '낭독할 문장을 확인하세요.') from None
    except tts_audio.SpeechBusy:
        raise HTTPException(503, '다른 문장을 읽고 있습니다.', headers={'Retry-After': '1'}) from None
    except tts_audio.SpeechUnavailable:
        raise HTTPException(503, 'PC의 한국어 음성을 사용할 수 없습니다.') from None
    return Response(audio, media_type=media_type, headers={'Cache-Control': 'no-store'})


@router.get('/hospital/', include_in_schema=False)
@router.get('/hospital/{asset}', include_in_schema=False)
def hospital(request: Request, asset: str = 'index.html'):
    local_only(request)
    if asset not in {'index.html', 'chat-recovery.mjs', 'inquiry-alerts.mjs', 'inquiry-layout.css',
                     'operations.mjs', 'review-discovery.mjs', 'workflow-panel.css',
                     'workflow-panel.mjs', 'workflow-state.mjs',
                     'panel.html', 'panel.css', 'panel.mjs', 'panel-state.mjs'} or not (HOSPITAL_UI / asset).is_file():
        raise HTTPException(404)
    # Keep the repository source untouched. Every original storage reference uses a
    # per-page RAM adapter, including ChatRecoveryStore's read-back verification.
    text = (HOSPITAL_UI / asset).read_text(encoding='utf-8').replace('localStorage', 'quick119MemoryStorage')
    if asset.endswith('.html'):
        text = text.replace('<head>', '<head><script src="/static/hospital-memory.js?v=2"></script>')
    if asset == 'index.html':
        text = text.replace("params.get('server') ||", '')
        text = text.replace("serverBase = $('server').value.trim().replace(/\\/$/, '');", "serverBase = location.origin;")
        text = text.replace("$('server').value = serverBase;", "$('server').value = serverBase; $('server').readOnly = true;")
        text = text.replace('`${serverBase}/patient/?pid=', '`${serverBase}/transport/?pid=')
        text = text.replace('</head>', '<link rel="stylesheet" href="/static/hospital-adapter.css?v=2"></head>')
        text = text.replace('<body>', '<body><aside class="synthetic-hospital-note">병원 이송 시연 · 미전송 초안과 재시도 요청은 새로고침하면 지워집니다.</aside>')
    media = 'text/html' if asset.endswith('.html') else 'text/css' if asset.endswith('.css') else 'text/javascript'
    return Response(text, media_type=media, headers={'Cache-Control': 'no-store',
        'Content-Security-Policy': "connect-src 'self'; object-src 'none'; base-uri 'self'"})
