from fastapi import FastAPI, Form, Query, HTTPException, Request
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager, closing
from settings import APP_ENV, DB_PATH, external_api_enabled, require_external_api, kakao_rest_api_key
import logging

from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse

import mimetypes
from fastapi.staticfiles import StaticFiles

from pathlib import Path

import math

import os

import json
import sqlite3

import urllib.parse

import urllib.request

import urllib.error

import xml.etree.ElementTree as ET

from urllib.parse import quote
from html import escape



# 운영 모드 또는 외부 API를 명시적으로 켠 개발 모드에서만 .env를 불러옵니다.

# .env 파일은 GitHub에 올리지 마세요.

def load_local_env():

    env_file = Path(__file__).resolve().parent / ".env"

    if not env_file.exists():

        return

    for line in env_file.read_text(encoding="utf-8-sig").splitlines():

        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:

            continue

        key, value = line.split("=", 1)

        key = key.strip()

        value = value.strip().strip('"').strip("'")

        if key and value:

            os.environ.setdefault(key, value)



# .env는 import 시 읽지 않습니다. 허용된 실행 모드에서 명시적으로 선택한 경우만 로드합니다.

from api_integrations import enrich_hospitals, enrich_hospitals_with_hira, enrich_hospitals_with_severe, kakao_route, kakao_routes



from database import create_database, save_patient, get_patients, get_patient
from database import save_emergency_intake
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID





@asynccontextmanager
async def lifespan(app):
    if os.getenv("QUICK119_LOAD_ENV", "0") == "1" and (
        APP_ENV == "production" or (APP_ENV == "development" and external_api_enabled())
    ):
        load_local_env()
    logging.getLogger('uvicorn.error').info(
        'Kakao Mobility: external_api_enabled=%s, key_loaded=%s',
        external_api_enabled(), bool(kakao_rest_api_key()))
    create_database()
    yield


app = FastAPI(lifespan=lifespan)

# Preparation only: disabled by default, synthetic loopback runtime required.
from tts_audio import neural_enabled
from team_chat_gateway import router as team_chat_router
app.include_router(team_chat_router)
from team_chat_ui import router as team_chat_ui_router, chat_ui_enabled
app.include_router(team_chat_ui_router)


class RoutePoint(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RouteDestination(RoutePoint):
    key: str = Field(min_length=1, max_length=64)


class CarRouteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    origin: RoutePoint
    destination: RoutePoint


class CarRoutesRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    origin: RoutePoint
    destinations: list[RouteDestination] = Field(min_length=1, max_length=5)


def _route_response(result):
    # Never forward the upstream response body, exception text or authorization header.
    from fastapi.responses import JSONResponse
    code = result['code']
    status = 200 if code in {'ok', 'no_route'} else {
        'disabled': 503, 'missing_key': 503, 'timeout': 504, 'quota_exceeded': 429,
    }.get(code, 502)
    return JSONResponse(result, status_code=status)


@app.post('/api/routes/car')
def car_route(payload: CarRouteRequest, request: Request):
    from synthetic_routes import demo_route
    demo = demo_route(request, payload)
    if demo is not None:
        return _route_response(demo)
    return _route_response(kakao_route(payload.origin.lat, payload.origin.lon,
                                     payload.destination.lat, payload.destination.lon, geometry=True))


@app.post('/api/routes/car/compare')
def compare_car_routes(payload: CarRoutesRequest):
    if len({point.key for point in payload.destinations}) != len(payload.destinations):
        raise HTTPException(status_code=422, detail='목적지 key는 중복될 수 없습니다.')
    return _route_response(kakao_routes(payload.origin.lat, payload.origin.lon,
                                       [point.model_dump() for point in payload.destinations]))


@app.middleware("http")
async def pwa_response_policy(request, call_next):
    response = await call_next(request)
    # 환자·문진·위치와 병원 응답을 HTTP 캐시에도 남기지 않습니다.
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(self), microphone=(self)"
    return response

# Presentation-only middleware: preserves existing endpoints, query parameters,
# forms, GPS scripts, API calls, and voice-report sessionStorage logic.
from starlette.responses import Response
import re

@app.middleware("http")
async def medical_ui_theme(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/static/", "/hospital/")) or request.url.path in {"/", "/emergency", "/pwa-check", "/voice-intake", "/transport/"}:
        return response
    if "text/html" not in response.headers.get("content-type", "").lower():
        return response
    payload = b"".join([part async for part in response.body_iterator])
    html = payload.decode("utf-8", errors="replace")
    theme = '<link rel="stylesheet" href="/static/medical-ui.css?v=2">'
    theme += templates.env.get_template("pwa_head.html").render()
    if not re.search(r'<meta\s+name=[\'"]theme-color', html, flags=re.I):
        theme += '<meta name="theme-color" content="#17375E">'
    if re.search(r"</head\s*>", html, flags=re.I):
        html = re.sub(r"</head\s*>", theme + "</head>", html, count=1, flags=re.I)
    else:
        html = theme + html
    current = request.url.path
    links = [("/", "홈"), ("/transport/", "접수 채팅"), ("/register", "환자 등록"), ("/patients", "환자 목록"), ("/emergency", "응급상황 접수")]
    menu = "".join('<a href="{}" {} class="{}">{}</a>'.format(
        href, 'aria-current="page"' if current == href else "",
        "med-emergency" if href == "/emergency" else "", title
    ) for href, title in links)
    header = '<header class="med-header"><nav class="med-nav" aria-label="주 메뉴"><a class="med-brand" href="/"><span class="med-brand-mark">✚</span><span>퀵119 <small style="font-size:10px;color:#8394a7;font-weight:600">QUICK119</small></span></a><div class="med-links">' + menu + '</div></nav></header>'
    banner = '<div class="med-banner"><div class="med-banner-inner"><strong>응급 안내</strong> · 위급한 상황에서는 앱 사용보다 <strong>119 신고를 우선</strong>하세요. 병상 정보는 실제 수용을 보장하지 않습니다.</div></div>'
    footer = '<footer class="med-footer">QUICK119 · 응급의료 정보 지원 캡스톤 프로젝트 · 음성 추출 정보는 의료 진단이 아닙니다.</footer>'
    footer += templates.env.get_template("pwa_controls.html").render()
    if re.search(r"<body\b[^>]*>", html, flags=re.I):
        html = re.sub(r"(<body\b[^>]*>)", lambda m:m.group(1)+header+banner, html, count=1, flags=re.I)
    if re.search(r"</body\s*>", html, flags=re.I):
        html = re.sub(r"</body\s*>", footer+"</body>", html, count=1, flags=re.I)
    headers = {k:v for k,v in response.headers.items() if k.lower() not in ("content-length","content-encoding","transfer-encoding")}
    return Response(content=html, status_code=response.status_code, headers=headers, media_type="text/html; charset=utf-8")




BASE_DIR = Path(__file__).resolve().parent



templates = Jinja2Templates(
    directory=BASE_DIR / "templates",
    context_processors=[lambda request: {"chat_ui_enabled": chat_ui_enabled(request)}],
)


@app.get("/manifest.webmanifest", include_in_schema=False)
def pwa_manifest():
    return FileResponse(BASE_DIR / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def pwa_service_worker():
    return FileResponse(BASE_DIR / "static" / "sw.js", media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-store"})


@app.get("/pwa-check", response_class=HTMLResponse)
def pwa_check_page(request: Request):
    return templates.TemplateResponse(request=request, name="pwa_check.html",
                                      context={"active_nav": "", "app_env": APP_ENV})





# ==================================================

# static 폴더 연결 (HTML 템플릿은 정적으로 공개하지 않습니다.)

# ==================================================



mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
app.mount(

    "/static",

    StaticFiles(directory=BASE_DIR / "static"),

    name="static"

)





# ==================================================

# 메인 화면

# ==================================================



@app.get("/", response_class=HTMLResponse)

def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"active_nav": "home", "app_env": APP_ENV})





# ==================================================

# 환자 등록 화면

# ==================================================



@app.get("/register", response_class=HTMLResponse)

def register_page():



    html_file = BASE_DIR / "templates" / "register.html"



    return html_file.read_text(encoding="utf-8")





# ==================================================

# 환자 등록 처리

# ==================================================



@app.post("/register")

def register_patient(

    name: str = Form(...),

    birth: str = Form(""),

    phone: str = Form(""),

    address: str = Form(""),

    guardian: str = Form(""),

    guardian_phone: str = Form("")

):



    save_patient(

        name,

        birth,

        phone,

        address,

        guardian,

        guardian_phone

    )



    return HTMLResponse("""

        <!DOCTYPE html>



        <html lang="ko">



        <head>

            <meta charset="UTF-8">

            <title>등록 완료</title>

        </head>



        <body>



            <h1>환자 등록 완료!</h1>



            <p>

                환자 정보가 데이터베이스에 저장되었습니다.

            </p>



            <a href="/register">

                다시 등록하기

            </a>



            <br><br>



            <a href="/patients">

                환자 목록 보기

            </a>



        </body>



        </html>

    """)





# ==================================================

# 환자 목록

# ==================================================



@app.get("/patients", response_class=HTMLResponse)
def patients_page():
    patients = get_patients()
    rows = []
    for patient in patients:
        patient_id = int(patient[0])
        name = escape(str(patient[1] or ""))
        birth = escape(str(patient[2] or "미등록"))
        phone = escape(str(patient[3] or "미등록"))
        rows.append(f"""
        <tr>
            <td data-label="번호">{patient_id}</td>
            <td data-label="환자명"><a class="q119-patient-link" href="/patients/{patient_id}">{name}</a></td>
            <td data-label="생년월일">{birth}</td>
            <td data-label="연락처">{phone}</td>
            <td data-label="관리" class="q119-actions">
                <a class="q119-btn q119-btn-outline" href="/patients/{patient_id}">상세 보기</a>
                <form method="post" action="/patients/{patient_id}/delete" onsubmit="return confirm('정말 이 환자를 삭제하시겠습니까?\\n삭제 후에는 복구할 수 없습니다.');">
                    <button class="q119-btn q119-btn-delete" type="submit">삭제</button>
                </form>
            </td>
        </tr>""")
    table_rows = "\n".join(rows) if rows else '<tr><td colspan="5" class="q119-empty">등록된 환자가 없습니다. 환자를 먼저 등록해 주세요.</td></tr>'
    return HTMLResponse(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>환자 목록 | 퀵119</title></head>
    <body><main class="q119-page">
      <div class="q119-page-heading"><div><p class="q119-eyebrow">PATIENT MANAGEMENT</p><h1>환자 목록</h1><p>등록된 환자를 확인하고 관리할 수 있습니다.</p></div><a class="q119-btn q119-btn-primary" href="/register">+ 환자 등록</a></div>
      <section class="q119-surface"><div class="q119-surface-heading"><h2>등록 환자 <span class="q119-count">{len(patients)}명</span></h2><p>삭제 버튼을 누르면 확인창이 나타납니다.</p></div>
      <div class="q119-table-scroll"><table class="q119-table"><thead><tr><th>번호</th><th>환자명</th><th>생년월일</th><th>연락처</th><th>관리</th></tr></thead><tbody>{table_rows}</tbody></table></div></section>
      <a class="q119-back" href="/">← 메인으로 돌아가기</a>
    </main></body></html>""")


@app.post("/patients/{patient_id}/delete")
def delete_patient(patient_id: int):
    # GET 요청으로는 삭제되지 않도록 POST 전용으로 처리합니다.
    if patient_id < 1:
        raise HTTPException(status_code=400, detail="잘못된 환자 번호입니다.")
    if get_patient(patient_id) is None:
        raise HTTPException(status_code=404, detail="환자를 찾을 수 없습니다.")
    db_path = DB_PATH
    with closing(sqlite3.connect(db_path, timeout=10)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            cursor = connection.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="환자를 찾을 수 없습니다.")
            connection.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="연결된 접수 기록이 있어 삭제할 수 없습니다.")
    return RedirectResponse(url="/patients", status_code=303)


# ==================================================



@app.get("/patients/{patient_id}", response_class=HTMLResponse)

def patient_detail(patient_id: int):



    patient = get_patient(patient_id)



    if patient is None:



        return HTMLResponse("""

            <h1>환자를 찾을 수 없습니다.</h1>



            <a href="/patients">

                환자 목록으로 돌아가기

            </a>

        """)



    return HTMLResponse(f"""

        <!DOCTYPE html>



        <html lang="ko">



        <head>



            <meta charset="UTF-8">



            <title>환자 상세정보</title>



            <style>



                body {{

                    font-family: Arial, sans-serif;

                    padding: 40px;

                    background-color: #f2f4f7;

                }}



                .container {{

                    max-width: 600px;

                    margin: auto;

                    padding: 30px;

                    background-color: white;

                    border-radius: 15px;

                }}



                .info {{

                    margin-bottom: 15px;

                    padding: 15px;

                    background-color: #f5f5f5;

                    border-radius: 8px;

                }}



                .emergency-button {{

                    display: block;

                    width: 100%;

                    padding: 15px;

                    margin-top: 30px;

                    background-color: #d32f2f;

                    color: white;

                    text-align: center;

                    text-decoration: none;

                    border-radius: 8px;

                    font-weight: bold;

                }}



            </style>



        </head>



        <body>



            <div class="container">



                <h1>환자 상세정보</h1>



                <div class="info">

                    이름: {patient[1]}

                </div>



                <div class="info">

                    생년월일: {patient[2] or "등록되지 않음"}

                </div>



                <div class="info">

                    전화번호: {patient[3] or "등록되지 않음"}

                </div>



                <div class="info">

                    주소: {patient[4] or "등록되지 않음"}

                </div>



                <div class="info">

                    보호자: {patient[5] or "등록되지 않음"}

                </div>



                <div class="info">

                    보호자 전화번호:

                    {patient[6] or "등록되지 않음"}

                </div>



                <a

                    class="emergency-button"

                    href="/emergency"

                >

                    🚨 응급상황 시작

                </a>



                <br>



                <a href="/patients">

                    환자 목록으로 돌아가기

                </a>



            </div>



        </body>



        </html>

    """)





# ==================================================

# 🚨 응급상황 시작

# ==================================================




@app.get("/voice-intake", response_class=HTMLResponse)
def voice_intake_page(request: Request):
    return RedirectResponse(url="/transport/", status_code=303)


class PatientState(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    age: str | None = Field(default=None, max_length=40)
    sex: str | None = Field(default=None, max_length=40)
    consciousness: str | None = Field(default=None, max_length=200)
    breathing: str | None = Field(default=None, max_length=200)
    symptoms: str | None = Field(default=None, max_length=2000)
    onset: str | None = Field(default=None, max_length=200)
    systolic: int | None = Field(default=None, ge=0, le=999)
    diastolic: int | None = Field(default=None, ge=0, le=999)
    pulse: int | None = Field(default=None, ge=0, le=999)
    temperature: float | None = Field(default=None, ge=0, le=60, allow_inf_nan=False)
    spo2: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class EmergencyIntake(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    patient_id: int | None = Field(default=None, gt=0)
    transcript: str = Field(min_length=1, max_length=20000)
    state: PatientState


@app.post("/api/emergency-intakes")
def create_emergency_intake(report: EmergencyIntake):
    try:
        record = save_emergency_intake(str(report.request_id), report.patient_id,
                                       report.transcript, report.state.model_dump())
    except LookupError:
        raise HTTPException(status_code=404, detail="선택한 등록 환자를 찾을 수 없습니다.")
    except ValueError:
        raise HTTPException(status_code=409, detail="저장 요청이 변경되었습니다. 내용을 확인하고 다시 저장해 주세요.")
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="서버에 저장하지 못했습니다. 내용을 유지한 채 다시 시도해 주세요.")
    return {"saved": True, **record}

@app.get("/emergency", response_class=HTMLResponse)
def emergency_page(request: Request, step: str = ""):
    return RedirectResponse(url="/transport/", status_code=303)


# ==================================================
# 의식 확인
# ==================================================



def quick119_intake_response(html: str):
    """Apply one consistent, mobile-first layout to all intake steps."""
    return HTMLResponse(html.replace("</head>", '<style id="quick119-intake-redesign">\n:root{--qnavy:#17375e;--qink:#1d2d42;--qmuted:#65758b;--qline:#e0e8f1;--qbg:#f5f8fc}\nhtml{width:100%;min-width:0}body{box-sizing:border-box!important;display:block!important;width:100%!important;min-width:0!important;min-height:100vh!important;height:auto!important;margin:0!important;padding:72px 16px 60px!important;background:var(--qbg)!important;color:var(--qink)!important;font-family:Pretendard,\'Noto Sans KR\',system-ui,sans-serif!important;text-align:left!important;line-height:1.55!important}\nbody::before{content:\'퀵119  /  QUICK119\';position:absolute;top:0;left:0;right:0;height:58px;display:flex;align-items:center;padding:0 max(20px,calc((100vw - 840px)/2));background:#fff;border-bottom:1px solid var(--qline);color:var(--qnavy);font-size:18px;font-weight:900;letter-spacing:-.4px}\nbody .container{box-sizing:border-box!important;display:block!important;width:100%!important;max-width:760px!important;min-width:0!important;height:auto!important;min-height:0!important;margin:22px auto 0!important;padding:clamp(22px,5vw,44px)!important;background:#fff!important;border:1px solid var(--qline)!important;border-radius:22px!important;box-shadow:0 12px 35px rgba(23,55,94,.055)!important;text-align:left!important;overflow:visible!important}\nbody .container .icon{display:none!important}body .container h1{font-size:clamp(25px,4vw,34px)!important;line-height:1.25!important;letter-spacing:-.7px!important;color:var(--qnavy)!important;text-align:left!important;margin:0 0 18px!important;overflow-wrap:anywhere}\nbody .container .patient{box-sizing:border-box!important;display:block!important;width:100%!important;padding:14px 18px!important;margin:0 0 24px!important;background:#edf4fd!important;border:1px solid #dce8f6!important;border-radius:12px!important;color:var(--qnavy)!important;text-align:left!important;font-size:15px!important;overflow-wrap:anywhere}\nbody .container .question{box-sizing:border-box!important;display:block!important;width:100%!important;min-height:0!important;margin:0 0 22px!important;padding:0!important;color:var(--qink)!important;font-size:clamp(18px,3vw,22px)!important;font-weight:800!important;line-height:1.55!important;text-align:left!important;overflow-wrap:anywhere}\nbody .container a.answer{box-sizing:border-box!important;display:flex!important;align-items:center!important;justify-content:center!important;width:100%!important;min-width:0!important;min-height:58px!important;margin:0 0 12px!important;padding:14px 20px!important;border-radius:13px!important;text-align:center!important;text-decoration:none!important;font-size:17px!important;font-weight:800!important;box-shadow:none!important;float:none!important;position:static!important}\nbody .container a.answer.yes{background:#17375e!important;color:#fff!important;border:1px solid #17375e!important}body .container a.answer.no{background:#fff1f1!important;color:#b42332!important;border:1px solid #f5cbd0!important}\nbody .container form{box-sizing:border-box!important;display:block!important;width:100%!important;min-width:0!important;margin:0!important;padding:0!important}\nbody .container .symptom-options{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:12px!important;width:100%!important;min-width:0!important;margin:0 0 24px!important;padding:0!important}\nbody .container label.symptom-choice,body .container label.choice{box-sizing:border-box!important;display:flex!important;align-items:center!important;justify-content:flex-start!important;gap:12px!important;width:100%!important;min-width:0!important;min-height:60px!important;margin:0!important;padding:14px 16px!important;background:#f8fafc!important;border:1px solid #dbe5f0!important;border-radius:13px!important;color:var(--qink)!important;text-align:left!important;font-size:15px!important;line-height:1.45!important;overflow-wrap:anywhere;cursor:pointer}\nbody .container label.symptom-choice:has(input:checked),body .container label.choice:has(input:checked){background:#eaf2fd!important;border-color:#376cb5!important;color:#17375e!important}\nbody .container label input[type=checkbox]{width:21px!important;height:21px!important;flex:0 0 21px!important;accent-color:#17375e!important;margin:0!important}\nbody .container label.choice{margin-bottom:10px!important}\nbody .container button[type=submit]{box-sizing:border-box!important;display:block!important;width:100%!important;min-width:0!important;min-height:56px!important;margin:20px 0 0!important;padding:14px 18px!important;border:0!important;border-radius:13px!important;background:#17375e!important;color:white!important;font-size:16px!important;font-weight:800!important;line-height:1.45!important;white-space:normal!important;cursor:pointer}\nbody .container p{color:var(--qmuted)!important;line-height:1.65!important;text-align:left!important;overflow-wrap:anywhere}\nbody .container svg{max-width:100%!important;height:auto!important}\n@media(max-width:600px){body{padding:65px 12px 32px!important}body::before{height:54px;padding:0 16px;font-size:16px}body .container{margin:12px auto 0!important;padding:22px 18px!important;border-radius:17px!important}body .container .symptom-options{grid-template-columns:1fr!important;gap:9px!important}body .container .question{font-size:18px!important}body .container a.answer{min-height:58px!important}}\n</style>'+"</head>", 1))

@app.get("/emergency/check", response_class=HTMLResponse)

def emergency_check(patient_id: str = ""):



    patient = None



    # 선택하지 않았거나 0이면 미등록 환자로 처리합니다.

    # HTML select에서 빈 문자열이 넘어와도 422 오류가 발생하지 않도록

    # 여기서 안전하게 숫자로 변환합니다.

    try:

        patient_id_int = int(patient_id) if patient_id.strip() else 0

    except ValueError:

        patient_id_int = 0



    if patient_id_int != 0:



        patient = get_patient(patient_id_int)



    if patient is None:



        patient_name = "미등록 환자"



        patient_id_value = "0"



    else:



        patient_name = patient[1]



        patient_id_value = str(patient[0])



    return quick119_intake_response(f"""

        <!DOCTYPE html>



        <html lang="ko">



        <head>



            <meta charset="UTF-8">



            <meta

                name="viewport"

                content="width=device-width, initial-scale=1.0"

            >



            <title>의식 확인</title>



            <style>



                * {{

                    box-sizing: border-box;

                }}



                body {{

                    margin: 0;



                    font-family: Arial, sans-serif;



                    background-color: #f2f4f7;



                    min-height: 100vh;



                    display: flex;



                    justify-content: center;



                    align-items: center;

                }}



                .container {{

                    width: 90%;



                    max-width: 500px;



                    background-color: white;



                    padding: 35px;



                    border-radius: 20px;



                    text-align: center;

                }}



                .icon {{

                    font-size: 60px;



                    margin-bottom: 20px;

                }}



                .patient {{

                    color: #555;



                    margin-bottom: 30px;

                }}



                .question {{

                    font-size: 22px;



                    font-weight: bold;



                    margin-bottom: 25px;

                }}



                .answer {{

                    display: block;



                    width: 100%;



                    padding: 18px;



                    margin-bottom: 15px;



                    border-radius: 12px;



                    text-decoration: none;



                    font-size: 18px;



                    font-weight: bold;

                }}



                .yes {{

                    background-color: #1976d2;



                    color: white;

                }}



                .no {{

                    background-color: #d32f2f;



                    color: white;

                }}



            </style>



        </head>



        <body>



            <div class="container">



                <div class="icon">

                    🚑

                </div>



                <h1>

                    현재 상태 확인

                </h1>



                <div class="patient">

                    환자:

                    <strong>{patient_name}</strong>

                </div>



                <div class="question">

                    환자가 의식이 있습니까?

                </div>



                <a

                    href="/emergency/breathing?patient_id={patient_id_value}&consciousness=yes"

                    class="answer yes"

                >

                    예

                </a>



                <a

                    href="/emergency/breathing?patient_id={patient_id_value}&consciousness=no"

                    class="answer no"

                >

                    아니오

                </a>



            </div>



        </body>



        </html>

    """)





# ==================================================

# 🫁 호흡 확인

# ==================================================



@app.get("/emergency/breathing", response_class=HTMLResponse)

def emergency_breathing(

    patient_id: int | None = None,

    consciousness: str = ""

):



    patient = None



    if patient_id is not None and patient_id != 0:



        patient = get_patient(patient_id)



    if patient is None:



        patient_name = "미등록 환자"



        patient_id_value = "0"



    else:



        patient_name = patient[1]



        patient_id_value = str(patient[0])



    return quick119_intake_response(f"""

        <!DOCTYPE html>



        <html lang="ko">



        <head>



            <meta charset="UTF-8">



            <meta

                name="viewport"

                content="width=device-width, initial-scale=1.0"

            >



            <title>호흡 확인</title>



            <style>



                * {{

                    box-sizing: border-box;

                }}



                body {{

                    margin: 0;



                    font-family: Arial, sans-serif;



                    background-color: #f2f4f7;



                    min-height: 100vh;



                    display: flex;



                    justify-content: center;



                    align-items: center;

                }}



                .container {{

                    width: 90%;



                    max-width: 500px;



                    background-color: white;



                    padding: 35px;



                    border-radius: 20px;



                    text-align: center;

                }}



                .icon {{

                    font-size: 60px;



                    margin-bottom: 20px;

                }}



                .patient {{

                    color: #555;



                    margin-bottom: 30px;

                }}



                .question {{

                    font-size: 22px;



                    font-weight: bold;



                    margin: 30px 0;

                }}



                .answer {{

                    display: block;



                    width: 100%;



                    padding: 18px;



                    margin-bottom: 15px;



                    border-radius: 12px;



                    text-decoration: none;



                    font-size: 18px;



                    font-weight: bold;

                }}



                .yes {{

                    background-color: #1976d2;



                    color: white;

                }}



                .no {{

                    background-color: #d32f2f;



                    color: white;

                }}



            </style>



        </head>



        <body>



            <div class="container">



                <div class="icon">

                    🫁

                </div>



                <h1>

                    호흡 확인

                </h1>



                <div class="patient">

                    환자:

                    <strong>{patient_name}</strong>

                </div>



                <div class="question">

                    환자가 정상적으로<br>

                    호흡하고 있습니까?

                </div>



                <a

                    href="/emergency/symptoms?patient_id={patient_id_value}&consciousness={consciousness}&breathing=yes"

                    class="answer yes"

                >

                    예

                </a>



                <a

                    href="/emergency/symptoms?patient_id={patient_id_value}&consciousness={consciousness}&breathing=no"

                    class="answer no"

                >

                    아니오

                </a>



            </div>



        </body>



        </html>

    """)





# ==================================================

# 🩺 증상 확인

# ==================================================



@app.get("/emergency/symptoms", response_class=HTMLResponse)

def emergency_symptoms(

    patient_id: int | None = None,

    consciousness: str = "",

    breathing: str = ""

):



    patient = None



    if patient_id is not None and patient_id != 0:



        patient = get_patient(patient_id)



    if patient is None:



        patient_name = "미등록 환자"



        patient_id_value = "0"



    else:



        patient_name = patient[1]



        patient_id_value = str(patient[0])



    return quick119_intake_response(f"""

        <!DOCTYPE html>



        <html lang="ko">



        <head>



            <meta charset="UTF-8">



            <meta

                name="viewport"

                content="width=device-width, initial-scale=1.0"

            >



            <title>증상 확인</title>



            <style>



                * {{

                    box-sizing: border-box;

                }}



                body {{

                    margin: 0;



                    font-family: Arial, sans-serif;



                    background-color: #f2f4f7;



                    min-height: 100vh;



                    display: flex;



                    justify-content: center;



                    align-items: center;

                }}



                .container {{

                    width: 90%;



                    max-width: 500px;



                    background-color: white;



                    padding: 35px;



                    border-radius: 20px;



                    text-align: center;

                }}



                .icon {{

                    font-size: 60px;



                    margin-bottom: 20px;

                }}



                .patient {{

                    color: #555;



                    margin-bottom: 30px;

                }}



                .question {{

                    font-size: 22px;



                    font-weight: bold;



                    margin: 30px 0;

                }}



                button {{

                    display: block;



                    width: 100%;



                    padding: 17px;



                    margin-bottom: 12px;



                    border: none;



                    border-radius: 12px;



                    background-color: #1976d2;



                    color: white;



                    font-size: 17px;



                    font-weight: bold;



                    cursor: pointer;

                }}



                button:hover {{

                    background-color: #125aa0;

                }}



                            .symptom-options {{ display:grid; gap:10px; margin-bottom:22px; text-align:left; }}

                .symptom-choice {{ display:flex; align-items:center; gap:12px; padding:16px; border:2px solid #d9e3f0; border-radius:12px; background:#f8fafc; font-size:17px; cursor:pointer; }}

                .symptom-choice:has(input:checked) {{ border-color:#1976d2; background:#e3f2fd; font-weight:bold; }}

                .symptom-choice input {{ width:21px; height:21px; accent-color:#1976d2; }}

</style>



        </head>



        <body>



            <div class="container">



                <div class="icon">

                    🩺

                </div>



                <h1>

                    증상 확인

                </h1>



                <div class="patient">

                    환자:

                    <strong>{patient_name}</strong>

                </div>



                <div class="question">해당하는 증상을 모두 선택해주세요.</div>

                <form action="/emergency/bodymap" method="get">

                    <input type="hidden" name="patient_id" value="{patient_id_value}">

                    <input type="hidden" name="consciousness" value="{consciousness}">

                    <input type="hidden" name="breathing" value="{breathing}">

                    <div class="symptom-options">

                        <label class="symptom-choice"><input type="checkbox" name="symptom" value="가슴 통증">❤️ 가슴 통증</label>

                        <label class="symptom-choice"><input type="checkbox" name="symptom" value="호흡 곤란">🫁 호흡 곤란</label>

                        <label class="symptom-choice"><input type="checkbox" name="symptom" value="출혈">🩸 출혈</label>

                        <label class="symptom-choice"><input type="checkbox" name="symptom" value="의식 저하">😵 의식 저하</label>

                        <label class="symptom-choice"><input type="checkbox" name="symptom" value="기타">➕ 기타</label>

                    </div>

                    <button type="submit">증상 선택 완료 · 다음으로</button>

                </form>



            </div>



        </body>



        </html>

    """)





# ==================================================
# 📍 아픈 부위 선택 (증상 선택과 별도 페이지)
# ==================================================

@app.get("/emergency/bodymap", response_class=HTMLResponse)
def emergency_bodymap(
    patient_id: int | None = None,
    consciousness: str = "",
    breathing: str = "",
    symptom: list[str] = Query(default=[]),
):
    allowed_symptoms = {"가슴 통증", "호흡 곤란", "출혈", "의식 저하", "기타"}
    selected_symptoms = list(dict.fromkeys(x for x in symptom if x in allowed_symptoms))
    patient = get_patient(patient_id) if patient_id else None
    patient_name = escape(str(patient[1])) if patient else "미등록 환자"
    patient_id_value = patient[0] if patient else 0
    hidden_symptoms = "".join(
        f'<input type="hidden" name="symptom" value="{escape(x, quote=True)}">'
        for x in selected_symptoms
    )
    return quick119_intake_response(f"""
    <!DOCTYPE html>
    <html lang="ko"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>아픈 부위 선택</title>
    <style>
    * {{box-sizing:border-box}}
    body {{margin:0;min-height:100vh;background:#f2f4f7;font-family:Arial,sans-serif;display:flex;justify-content:center;align-items:center;padding:22px 0}}
    .container {{width:90%;max-width:500px;background:white;border-radius:20px;padding:32px;text-align:center}}
    .choice {{display:flex;gap:12px;align-items:center;text-align:left;padding:14px;margin-bottom:9px;border:2px solid #d9e3f0;border-radius:12px;background:#f8fafc;cursor:pointer}}
    .choice:has(input:checked) {{background:#e3f2fd;border-color:#1976d2;font-weight:bold}}
    input[type=checkbox] {{width:21px;height:21px;accent-color:#1976d2}}
    button {{width:100%;padding:17px;border:0;border-radius:12px;background:#1976d2;color:white;font-size:17px;font-weight:bold;cursor:pointer;margin-top:14px}}
    p {{color:#64748b;line-height:1.6}}
    </style></head><body>
    <div class="container">
      <h1>📍 아픈 부위 선택</h1>
      <p>환자: <strong>{patient_name}</strong></p>
      <p>아픈 부위를 모두 선택해 주세요. 해당 사항이 없다면 선택하지 않고 진행할 수 있습니다.</p>
      <svg viewBox="0 0 160 245" width="160" height="245" role="img" aria-label="인체 앞면 안내 그림" style="display:block;margin:0 auto 15px;">
        <circle cx="80" cy="26" r="19" fill="#cbd5e1" stroke="#64748b"/>
        <path d="M66 47 L49 55 L35 118 L45 123 L61 79 L62 145 L65 227 L78 227 L82 153 L86 227 L99 227 L102 145 L103 79 L119 123 L129 118 L111 55 L94 47 Z" fill="#cbd5e1" stroke="#64748b" stroke-width="2"/>
        <text x="80" y="30" text-anchor="middle" font-size="10">머리</text>
        <text x="80" y="88" text-anchor="middle" font-size="10">가슴</text>
        <text x="80" y="126" text-anchor="middle" font-size="10">복부</text>
      </svg>
      <form action="/emergency/report" method="get">
        <input type="hidden" name="patient_id" value="{patient_id_value}">
        <input type="hidden" name="consciousness" value="{escape(consciousness, quote=True)}">
        <input type="hidden" name="breathing" value="{escape(breathing, quote=True)}">
        {hidden_symptoms}
        <label class="choice"><input type="checkbox" name="body_part" value="머리">머리</label>
        <label class="choice"><input type="checkbox" name="body_part" value="가슴">가슴</label>
        <label class="choice"><input type="checkbox" name="body_part" value="복부">복부</label>
        <label class="choice"><input type="checkbox" name="body_part" value="오른팔">오른팔</label>
        <label class="choice"><input type="checkbox" name="body_part" value="왼팔">왼팔</label>
        <label class="choice"><input type="checkbox" name="body_part" value="오른다리">오른다리</label>
        <label class="choice"><input type="checkbox" name="body_part" value="왼다리">왼다리</label>
        <button type="submit">선택 완료 · 구급대 전달 정보 확인</button>
      </form>
    </div></body></html>
    """)


# ==================================================

# 🏥 전국 응급의료기관 API 연동

# 공공데이터포털: 국립중앙의료원 전국 응급의료기관 정보 조회 서비스

# 인증키는 환경변수 EMERGENCY_API_KEY에 보관합니다.

# ==================================================



API_BASE = "https://apis.data.go.kr/B552657/ErmctInfoInqireService"


class EmergencyAPIResponseError(ValueError):
    """외부 응답 원문/요청 URL 대신 검증된 숫자 코드와 고정 안내만 노출합니다."""

    def __init__(self, code):
        safe_code = code if re.fullmatch(r"[0-9]{1,3}", code) else "미확인"
        super().__init__(
            f"공공 API 오류 (코드 {safe_code}). "
            "인증키의 유효성, 해당 서비스·상세기능 이용 승인, 호출 한도를 확인해주세요."
        )





def calculate_distance_km(lat1, lon1, lat2, lon2):

    """두 GPS 좌표 사이의 대략적인 직선거리를 km 단위로 계산합니다."""

    earth_radius_km = 6371

    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])

    dlat = lat2 - lat1

    dlon = lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2

    return earth_radius_km * 2 * math.asin(math.sqrt(a))





def reverse_geocode(lat, lon):
    if not external_api_enabled():
        return None, None

    """GPS 좌표를 시도/시군구로 변환합니다. 외부 지오코딩 서비스 장애 시 None을 반환합니다."""

    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode({

        "format": "jsonv2", "lat": lat, "lon": lon, "zoom": 10, "addressdetails": 1, "accept-language": "ko"

    })

    request = urllib.request.Request(url, headers={"User-Agent": "EmergencyPatientSupportCapstone/1.0 (student project)"})

    try:

        with urllib.request.urlopen(request, timeout=8) as response:

            payload = json.loads(response.read().decode("utf-8"))

        address = payload.get("address", {})

        stage1 = address.get("state") or address.get("province") or address.get("city")

        stage2 = address.get("city_district") or address.get("county") or address.get("city") or address.get("town") or address.get("municipality")

        if not stage1 or not stage2:

            return None, None

        # 국내 API에서 자주 사용하는 행정구역 명칭 형태로 정리합니다.

        stage1_aliases = {

            "서울": "서울특별시", "서울특별시": "서울특별시",

            "부산": "부산광역시", "부산광역시": "부산광역시",

            "대구": "대구광역시", "대구광역시": "대구광역시",

            "인천": "인천광역시", "인천광역시": "인천광역시",

            "광주": "광주광역시", "광주광역시": "광주광역시",

            "대전": "대전광역시", "대전광역시": "대전광역시",

            "울산": "울산광역시", "울산광역시": "울산광역시",

            "세종": "세종특별자치시", "세종특별자치시": "세종특별자치시",

            "경기도": "경기도", "강원특별자치도": "강원도", "강원도": "강원도",

            "충청북도": "충청북도", "충청남도": "충청남도", "전북특별자치도": "전라북도",

            "전라북도": "전라북도", "전라남도": "전라남도", "경상북도": "경상북도",

            "경상남도": "경상남도", "제주특별자치도": "제주특별자치도"

        }

        stage1 = stage1_aliases.get(stage1, stage1)

        # 응급의료 API는 시군구 공백을 생략한 이름도 사용하므로 함께 정리합니다.

        stage2 = stage2.replace(" ", "")

        return stage1, stage2

    except Exception:

        return None, None





def api_xml(operation, params, timeout=10):
    require_external_api()

    """공공데이터 XML을 요청하고 응답 오류를 검사합니다."""

    url = API_BASE + "/" + operation + "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(

        url, headers={"User-Agent": "EmergencyPatientSupportCapstone/1.0"}

    )

    with urllib.request.urlopen(request, timeout=timeout) as response:

        root = ET.fromstring(response.read())

    code = (root.findtext(".//header/resultCode") or
            root.findtext(".//cmmMsgHeader/returnReasonCode") or "").strip()

    if code and code not in {"00", "0"}:
        raise EmergencyAPIResponseError(code)

    return root





def fetch_hospital_basic(hpid, api_key):

    """실시간 병상 API에 없는 주소/좌표/진료과를 기관 기본정보에서 조회합니다."""

    root = api_xml("getEgytBassInfoInqire",

                   {"serviceKey": api_key, "HPID": hpid}, timeout=6)

    item = root.find(".//body/items/item")

    if item is None:

        return None



    lat_text = (item.findtext("wgs84Lat") or "").strip()

    lon_text = (item.findtext("wgs84Lon") or "").strip()

    try:

        lat, lon = float(lat_text), float(lon_text)

        if not (33 <= lat <= 39 and 124 <= lon <= 132):

            lat, lon = None, None

    except ValueError:

        lat, lon = None, None



    departments = []

    # dg1\~dg12는 서비스 응답에 따라 코드/설명일 수 있으므로

    # 명확한 과목명이 아닌 값은 임의로 해석하지 않습니다.

    for i in range(1, 13):

        value = (item.findtext(f"dg{i}") or "").strip()

        if value and value not in ("Y", "N", "1", "0") and value not in departments:

            departments.append(value)



    return {

        "lat": lat, "lon": lon,

        "address": (item.findtext("dutyAddr") or "").strip(),

        "phone": (item.findtext("dutyTel3") or "").strip(),

        "departments": ", ".join(departments) if departments else "미확인",

    }





def fetch_emergency_hospitals(lat, lon):
    if not external_api_enabled():
        return [], "현재 실행 설정에서는 실제 병원 API를 조회하지 않습니다. 테스트 병원 데이터도 표시하지 않습니다."

    """실시간 병상 데이터와 병원 기본정보를 HPID로 연결합니다."""

    api_key = os.getenv("EMERGENCY_API_KEY", "").strip()

    if not api_key:

        return [], "EMERGENCY_API_KEY가 설정되지 않았습니다."



    stage1, stage2 = reverse_geocode(lat, lon)

    if not stage1 or not stage2:

        return [], "GPS 좌표의 행정구역을 확인하지 못했습니다."



    # 확인된 실시간 API는 '경기', '성남시'처럼 축약 시도명을 허용합니다.

    stage1_short = {

        "경기도": "경기", "서울특별시": "서울", "부산광역시": "부산",

        "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",

        "대전광역시": "대전", "울산광역시": "울산",

        "강원도": "강원", "충청북도": "충북", "충청남도": "충남",

        "전라북도": "전북", "전라남도": "전남",

        "경상북도": "경북", "경상남도": "경남",

        "제주특별자치도": "제주", "세종특별자치시": "세종",

    }.get(stage1, stage1)



    try:

        root = api_xml(

            "getEmrrmRltmUsefulSckbdInfoInqire",

            {"serviceKey": api_key, "STAGE1": stage1_short,

             "STAGE2": stage2, "pageNo": 1, "numOfRows": 100},

            timeout=15

        )

        items = root.findall(".//body/items/item")

        if not items:

            return [], f"{stage1} {stage2}: 실시간 응급실 데이터가 없습니다."



        hospitals = []

        errors = 0

        # 먼저 실시간 API로 병원 식별번호를 가져오고,

        # 기관 기본정보 API에서 좌표/주소를 보완합니다.

        for item in items[:30]:

            hpid = (item.findtext("hpid") or "").strip()

            if not hpid:

                continue

            try:

                basic = fetch_hospital_basic(hpid, api_key)

            except Exception:

                errors += 1

                continue

            if not basic or basic["lat"] is None or basic["lon"] is None:

                continue



            beds_raw = (item.findtext("hvec") or "").strip()

            try:

                beds_num = int(beds_raw)

                beds = str(beds_num) if beds_num >= 0 else "미확인 (음수 값)"

            except ValueError:

                beds = "미확인"



            # 실시간 API의 원본 필드: 응급실 병상과 같은 응답에서 추가 자원 확인
            # 음수는 가용 자원으로 오해하지 않도록 그대로 수치화하지 않습니다.
            resource_fields = {
                "operating_room": "hvoc",
                "icu_general": "hvicc",
                "icu_neuro": "hvcc",
            }
            resource_values = {}
            for label, tag in resource_fields.items():
                raw = (item.findtext(tag) or "").strip()
                try:
                    number = int(raw)
                    resource_values[label] = str(number) if number >= 0 else "미확인 (음수 값)"
                except ValueError:
                    resource_values[label] = "미확인"

            def availability(tag):
                raw = (item.findtext(tag) or "").strip().upper()
                if raw == "Y":
                    return "가능 표시 (병원 확인 필요)"
                if raw == "N":
                    return "불가 표시"
                return "미확인"

            resource_values["ct"] = availability("hvctayn")
            resource_values["mri"] = availability("hvmriayn")

            hospitals.append({

                "name": (item.findtext("dutyName") or "이름 미확인").strip(),

                "lat": basic["lat"], "lon": basic["lon"],

                "emergency_beds": beds,
                "resources": resource_values,

                "address": basic["address"] or "주소 미확인",

                "phone": basic["phone"] or (item.findtext("dutyTel3") or ""),

                "departments": basic["departments"],

                "hpid": hpid

            })



        if not hospitals:

            return [], (

                f"{stage1} {stage2} 실시간 데이터 {len(items)}건은 받았으나 "

                f"병원 기본정보/좌표 연결에 실패했습니다. "

                f"(기본정보 API 요청 실패: {errors}건) "

                "해당 API의 기본정보 조회 권한과 응답 필드를 확인해주세요."

            )



        hospitals.sort(

            key=lambda h: calculate_distance_km(lat, lon, h["lat"], h["lon"])

        )

        return hospitals[:5], None

    except urllib.error.HTTPError as e:

        if e.code in {401, 403}:
            return [], f"공공 API HTTP 오류: {e.code}. 인증키와 해당 서비스 이용 승인을 확인해주세요."
        return [], f"공공 API HTTP 오류: {e.code}. 서버 연결 상태와 서비스 상태를 확인해주세요."

    except urllib.error.URLError:

        return [], "공공 API 네트워크 연결에 실패했습니다. 서버의 인터넷 연결, DNS, TLS 인증서 신뢰를 확인해주세요."

    except TimeoutError:
        return [], "공공 API 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요."

    except EmergencyAPIResponseError as e:
        return [], str(e)

    except (ET.ParseError, ValueError):
        return [], "공공 API 응답 처리 오류: XML 형식 또는 병원 데이터 값이 올바르지 않습니다."

    except Exception as e:

        return [], f"병원 데이터 처리 오류: {type(e).__name__}"





# ==================================================

# 📋 구급대 전달 정보

# ==================================================



@app.get("/emergency/report", response_class=HTMLResponse)
def emergency_report(
    patient_id: int | None = None,
    consciousness: str = "",
    breathing: str = "",
    symptom: list[str] = Query(default=[]),
    body_part: list[str] = Query(default=[]),
):
    """구급대 전달용 환자 요약. 병원 조회는 다음 페이지에서 수행."""
    patient = get_patient(patient_id) if patient_id and patient_id > 0 else None
    fields = (
        ("이름", patient[1] if patient else "미등록 환자"),
        ("생년월일", patient[2] if patient else "등록되지 않음"),
        ("전화번호", patient[3] if patient else "등록되지 않음"),
        ("주소", patient[4] if patient else "등록되지 않음"),
        ("보호자", patient[5] if patient else "등록되지 않음"),
        ("보호자 전화번호", patient[6] if patient else "등록되지 않음"),
        ("의식", "있음" if consciousness == "yes" else "없음" if consciousness == "no" else "미확인"),
        ("호흡", "정상" if breathing == "yes" else "비정상" if breathing == "no" else "미확인"),
        ("주요 증상", ", ".join(symptom) or "선택 없음"),
        ("아픈 부위", ", ".join(body_part) or "선택 없음"),
    )
    rows = "".join(
        '<div class="info"><span class="label">' + escape(label) + '</span>'
        + '<strong>' + escape(str(value or "등록되지 않음")) + '</strong></div>'
        for label, value in fields
    )
    hidden = "".join(
        '<input type="hidden" name="' + escape(key, quote=True)
        + '" value="' + escape(str(value), quote=True) + '">'
        for key, values in (
            ("patient_id", [patient_id or 0]),
            ("consciousness", [consciousness]),
            ("breathing", [breathing]),
            ("symptom", symptom),
            ("body_part", body_part),
        )
        for value in values
    )
    return HTMLResponse("""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>구급대 전달 정보</title>
<style>
*{box-sizing:border-box}body{margin:0;padding:24px 12px;background:#f2f4f7;font-family:Arial,sans-serif}
.container{max-width:520px;margin:auto;background:white;border-radius:18px;padding:28px}
h1{text-align:center;font-size:25px}.description{text-align:center;color:#64748b;line-height:1.6}
.info{display:flex;justify-content:space-between;gap:16px;padding:15px 8px;border-bottom:1px solid #e5e7eb;line-height:1.5}
.label{color:#64748b;min-width:110px}.info strong{text-align:right;overflow-wrap:anywhere}
button{display:block;width:100%;padding:17px;background:#1976d2;color:white;border:0;border-radius:12px;font-size:17px;font-weight:bold;cursor:pointer;margin-top:24px}
.notice{color:#b91c1c;font-size:13px;line-height:1.5;margin-top:20px}
</style></head><body><main class="container"><h1>🚑 구급대 전달 정보</h1>
<p class="description">현재 확인된 환자 정보와 응급상태를 정리했습니다.</p>
""" + rows + """
<section id="voice-report" style="display:none;margin-top:24px;padding:18px;border:1px solid #bfdbfe;border-radius:12px;background:#eff6ff">
<h2 style="margin:0 0 10px;font-size:19px">🎤 음성 문진으로 확인한 정보</h2>
<p style="font-size:12px;color:#475569;line-height:1.5">음성에서 자동 추출한 내용입니다. 환자·보호자 진술을 바탕으로 하며 진단이나 검증된 병력이 아닙니다.</p>
<div id="voice-report-fields"></div>
<p style="font-size:12px;color:#b45309;line-height:1.5">오인식·누락 가능성이 있으므로 구급대에 전달하기 전에 확인해 주세요.</p>
<button type="button" id="voice-report-clear" style="background:#64748b;padding:10px;font-size:14px;margin-top:10px">음성 문진 정보 지우기</button>
</section>
<script>
(function(){
  const container=document.getElementById('voice-report');
  const fields=document.getElementById('voice-report-fields');
  let data;
  try { data=JSON.parse(sessionStorage.getItem('emergencyVoiceIntake')||'null'); } catch(e) { return; }
  if(!data || typeof data!=='object' || !data.transcript) return;
  const entries=[['기존 질환 언급',data.conditions],['통증 부위',data.areas],['현재 증상',data.symptoms],['발생 시점',data.onset],['환자·보호자 진술 원문',data.transcript]];
  for(const [label,value] of entries){
    const row=document.createElement('div');row.className='info';
    const key=document.createElement('span');key.className='label';key.textContent=label;
    const val=document.createElement('strong');val.textContent=typeof value==='string'?value:'확인 필요';
    row.append(key,val);fields.appendChild(row);
  }
  container.style.display='block';
  document.getElementById('voice-report-clear').addEventListener('click',()=>{
    sessionStorage.removeItem('emergencyVoiceIntake');container.style.display='none';fields.replaceChildren();
  });
})();
</script>
<p class="notice">의식이 없거나 호흡이 비정상이라면 추가 입력보다 즉시 119 신고와 안내를 우선하세요.</p>
<form action="/emergency/hospitals" method="get">""" + hidden + """
<button type="submit">📍 다음: 현재 위치 및 주변 응급실</button></form>
</main></body></html>""")


@app.get("/emergency/hospitals", response_class=HTMLResponse)

def emergency_hospitals(
    request: Request,

    patient_id: int | None = None,

    consciousness: str = "",

    breathing: str = "",

    symptom: list[str] = Query(default=[]),

    body_part: list[str] = Query(default=[]),

    latitude: float | None = None,

    longitude: float | None = None,

    accuracy: float | None = None

):



    patient = None



    if patient_id is not None and patient_id != 0:

        patient = get_patient(patient_id)



    if patient is None:



        patient_name = "미등록 환자"

        birth = "등록되지 않음"

        phone = "등록되지 않음"

        address = "등록되지 않음"

        guardian = "등록되지 않음"

        guardian_phone = "등록되지 않음"



    else:



        patient_name = patient[1]

        birth = patient[2] or "등록되지 않음"

        phone = patient[3] or "등록되지 않음"

        address = patient[4] or "등록되지 않음"

        guardian = patient[5] or "등록되지 않음"

        guardian_phone = patient[6] or "등록되지 않음"



    if consciousness == "yes":

        consciousness_text = "있음"

        consciousness_class = "normal"

    else:

        consciousness_text = "없음" if consciousness == "no" else "미확인"

        consciousness_class = "warning"



    if breathing == "yes":

        breathing_text = "정상"

        breathing_class = "normal"

    else:

        breathing_text = "비정상" if breathing == "no" else "미확인"

        breathing_class = "warning"



    allowed_symptoms = {"가슴 통증", "호흡 곤란", "출혈", "의식 저하", "기타"}
    selected_symptoms = list(dict.fromkeys(x for x in symptom if x in allowed_symptoms))
    symptom_text = ", ".join(selected_symptoms) if selected_symptoms else "확인되지 않음"
    allowed_parts = {"머리", "가슴", "복부", "오른팔", "왼팔", "오른다리", "왼다리"}
    selected_parts = list(dict.fromkeys(x for x in body_part if x in allowed_parts))
    body_part_text = ", ".join(selected_parts) if selected_parts else "선택하지 않음"



    if latitude is not None and longitude is not None:

        sorted_hospitals, hospital_error = fetch_emergency_hospitals(latitude, longitude)

        if sorted_hospitals:
            sorted_hospitals = enrich_hospitals(sorted_hospitals, latitude, longitude)
            sorted_hospitals = enrich_hospitals_with_hira(sorted_hospitals)
            severe_stage1, severe_stage2 = reverse_geocode(latitude, longitude)
            severe_stage1 = {"경기도":"경기", "서울특별시":"서울", "인천광역시":"인천", "부산광역시":"부산", "대구광역시":"대구", "광주광역시":"광주", "대전광역시":"대전", "울산광역시":"울산", "세종특별자치시":"세종", "충청북도":"충북", "충청남도":"충남", "전라북도":"전북", "전북특별자치도":"전북", "전라남도":"전남", "경상북도":"경북", "경상남도":"경남", "강원특별자치도":"강원", "강원도":"강원", "제주특별자치도":"제주"}.get(severe_stage1, severe_stage1)
            sorted_hospitals = enrich_hospitals_with_severe(sorted_hospitals, severe_stage1, severe_stage2)

            hospital_cards = ""

            for hospital in sorted_hospitals[:5]:

                distance = calculate_distance_km(

                    latitude, longitude, hospital["lat"], hospital["lon"]

                )

                phone_html = f'<div class="hospital-detail"><strong>응급실 전화:</strong> {hospital["phone"]}</div>' if hospital["phone"] else ""

                road = hospital.get("road", {})
                if road.get("status") == "조회됨":
                    road_html = (f'<div class="hospital-detail"><strong>도로거리:</strong> '
                                 f'{road["distance_km"]}km · <strong>예상 이동시간:</strong> '
                                 f'약 {road["duration_min"]}분 (일반 차량 기준)</div>')
                else:
                    road_html = (f'<div class="hospital-detail"><strong>도로거리·예상시간:</strong> '
                                 f'{escape(road.get("status", "확인 불가"))}</div>')
                hira = hospital.get("hira", {})
                if hira.get("status") == "조회됨":
                    dept_items = hira.get("departments", [])
                    dept_labels = [escape(d["name"]) + (f" (전문의 {d['specialists']}명)" if d.get("specialists") is not None else " (전문의 수 미확인)") for d in dept_items]
                    dept_text = ', '.join(dept_labels) if dept_labels else escape(hira.get("department_status", "진료과목 응답 없음"))
                    facility = hira.get("facility", {})
                    if facility.get("status") == "조회됨":
                        facility_text = ', '.join(escape(k) + f" {v}" + ("실" if k in ("수술실", "분만실", "물리치료실") else "병상") for k, v in facility.get("items", {}).items())
                    else:
                        facility_text = escape(facility.get("status", "미확인"))
                    equipment = hira.get("equipment", {})
                    if equipment.get("status") == "조회됨":
                        equipment_text = ', '.join(escape(e["name"]) + f" {e['count']}대" for e in equipment.get("items", []))
                    else:
                        equipment_text = escape(equipment.get("status", "미확인"))
                    hira_html = (
                        '<details class="hospital-detail"><summary><strong>진료과·전문의 / 시설·장비 보유 현황 보기</strong></summary>'
                        + '<div class="hospital-detail"><strong>심평원 진료과·전문의 수 (현재 근무 인원 아님):</strong> ' + dept_text + '</div>'
                        + '<div class="hospital-detail"><strong>시설 보유 현황 (실시간 가용 아님):</strong> ' + facility_text + '</div>'
                        + '<div class="hospital-detail"><strong>의료장비 보유 현황 (실시간 가용 아님):</strong> ' + equipment_text + '</div></details>'
                    )
                else:
                    hira_html = ('<div class="hospital-detail muted"><strong>심평원 진료과·시설·장비:</strong> '
                                 + escape(hira.get("status", "미확인")) + '</div>')
                # V9: 증상은 진단이 아니며, 진료과 개설 여부는 응급 수용 가능 여부가 아닙니다.
                # 중증질환 MKioskTy 코드는 공식 매핑 전까지 자동 매칭하지 않습니다.
                symptom_department_map = {
                    "가슴 통증": ("응급의학과", "내과", "심장혈관흉부외과"),
                    "호흡 곤란": ("응급의학과", "내과", "심장혈관흉부외과"),
                    "출혈": ("응급의학과", "외과"),
                    "의식 저하": ("응급의학과", "신경과", "신경외과"),
                    "기타": ("응급의학과",),
                }
                selected_symptoms = [s.strip() for s in symptom_text.split(",") if s.strip() in symptom_department_map]
                relevant_departments = list(dict.fromkeys(
                    department for symptom_name in selected_symptoms
                    for department in symptom_department_map[symptom_name]
                ))
                available_departments = {
                    str(d.get("name", "")).strip() for d in hira.get("departments", [])
                } if hira.get("status") == "조회됨" else set()
                if relevant_departments:
                    department_rows = []
                    for department in relevant_departments:
                        if hira.get("status") != "조회됨":
                            state = "개설 여부 미확인"
                        elif department in available_departments:
                            state = "개설 정보 있음 · 현재 진료 가능 여부 미확인"
                        else:
                            state = "개설 정보 확인 안 됨 · 진료 불가 단정 금지"
                        department_rows.append(
                            '<div class="hospital-detail">' + escape(department) + ': ' + escape(state) + '</div>'
                        )
                    symptom_support_html = (
                        '<details class="hospital-detail" open><summary><strong>환자 증상 관련 진료과 참고정보</strong></summary>'
                        + '<div class="hospital-detail"><strong>선택 증상:</strong> '
                        + escape(', '.join(selected_symptoms)) + '</div>'
                        + ''.join(department_rows)
                        + '<div class="muted">증상-진료과 연결은 안내용 예시입니다. 질환 진단, 응급 수용 가능 판정, 병원 추천 순위에 사용하지 않습니다.</div>'
                        + '</details>'
                    )
                else:
                    symptom_support_html = (
                        '<div class="hospital-detail muted">선택된 증상이 없어 관련 진료과 참고정보를 표시하지 않습니다.</div>'
                    )
                verification_html = (
                    '<details class="hospital-detail"><summary><strong>119·병원에 확인할 사항</strong></summary>'
                    '<div class="hospital-detail">① 현재 환자의 증상·중증도에 따른 실제 수용 가능 여부</div>'
                    '<div class="hospital-detail">② 필요한 전문의·검사·처치 가능 여부와 예상 대기시간</div>'
                    '<div class="hospital-detail">③ 응급실·중환자실·수술실 실제 이용 가능 여부</div>'
                    '<div class="hospital-detail">④ 중증질환 API의 세부 제한 조건 및 이송 전 연락 필요 여부</div>'
                    '<div class="muted">응급상황에서는 119의 판단과 지시에 따르세요.</div></details>'
                )
                resources = hospital.get("resources", {})
                # 실시간 자원 수치는 시설 보유 개수와 다를 수 있으며, 수용 확약이 아닙니다.
                resource_html = (
                    '<details class="hospital-detail"><summary><strong>수술실·중환자실·CT/MRI 자원정보 보기</strong></summary>'
                    + '<div class="hospital-detail"><strong>수술실 가용 정보 (hvoc):</strong> '
                    + escape(resources.get("operating_room", "미확인")) + '</div>'
                    + '<div class="hospital-detail"><strong>일반 중환자실 가용 정보 (hvicc):</strong> '
                    + escape(resources.get("icu_general", "미확인")) + '</div>'
                    + '<div class="hospital-detail"><strong>신경 중환자실 가용 정보 (hvcc):</strong> '
                    + escape(resources.get("icu_neuro", "미확인")) + '</div>'
                    + '<div class="hospital-detail"><strong>CT 장비 가용 표시:</strong> '
                    + escape(resources.get("ct", "미확인")) + '</div>'
                    + '<div class="hospital-detail"><strong>MRI 장비 가용 표시:</strong> '
                    + escape(resources.get("mri", "미확인")) + '</div>'
                    + '<div class="muted">공공 API 보고값이며 실제 수용 가능 여부는 병원에 확인해야 합니다.</div></details>'
                )
                # V7: 공식 질환 코드표 확인 전에는 명칭이나 증상별 적합도를 추정하지 않습니다.
                severe = hospital.get("severe", {})
                if severe.get("status") == "조회됨":
                    entries = severe.get("items", [])
                    yes_count = sum(1 for e in entries if str(e.get("value", "")).upper() == "Y")
                    no_count = sum(1 for e in entries if str(e.get("value", "")).upper() == "N")
                    unknown_count = len(entries) - yes_count - no_count
                    notes = [e for e in entries if e.get("message")]
                    severe_parts = []
                    for entry in entries:
                        raw = str(entry.get("value", "정보미제공"))
                        label = ("가능 표시 · 확인 필요" if raw.upper() == "Y" else
                                 "불가 표시" if raw.upper() == "N" else "정보 없음 · 불가 아님")
                        code = escape(str(entry.get("code", "")))
                        msg = escape(str(entry.get("message", "")))
                        extra = ('<div style="margin-top:5px;color:#7c2d12;">⚠️ 조건·참고: ' + msg + '</div>') if msg else ''
                        severe_parts.append('<div style="padding:9px 0;border-bottom:1px solid #e5e7eb;">'
                                            + '<strong>' + code + '</strong> · ' + escape(label) + extra + '</div>')
                    note_html = ''.join('<div style="padding:7px 0;border-bottom:1px solid #f1f5f9;">'
                                        + '<strong>' + escape(str(e.get("code", ""))) + '</strong>: '
                                        + escape(str(e.get("message", ""))) + '</div>' for e in notes)
                    if not note_html:
                        note_html = '<div>별도로 제공된 제한·참고 설명 없음 (제한이 없다는 의미는 아님)</div>'
                    severe_html = (
                        '<div class="hospital-detail"><strong>중증질환 수용정보 요약 (코드 기준)</strong>'
                        + '<div style="margin-top:7px;">가능 표시 ' + str(yes_count)
                        + '개 · 불가 표시 ' + str(no_count) + '개 · 정보 없음/기타 '
                        + str(unknown_count) + '개</div>'
                        + '<div class="muted" style="margin-top:5px;">※ 각 코드의 질환명은 공식 매핑 미확인. 숫자는 병원의 전체 수용능력 점수가 아닙니다.</div></div>'
                        + (('<details class="hospital-detail"><summary><strong>⚠️ 제한 조건·참고 설명 ('
                        + str(len(notes)) + '건) 펼쳐보기</strong></summary><div>' + note_html + '</div></details>') if notes else '')
                        + '<details class="hospital-detail"><summary><strong>전체 중증질환 코드 ('
                        + str(len(entries)) + '건) 펼쳐보기</strong></summary><div>'
                        + ''.join(severe_parts) + '</div></details>'
                    )
                else:
                    severe_html = ('<div class="hospital-detail"><strong>중증질환 수용정보:</strong> '
                                   + escape(severe.get("status", "미확인")) + '</div>')
                severe_html += '<div class="hospital-detail muted">중증질환 코드는 공식 명칭 확인 전입니다. Y는 수용 확약이 아니며, 정보 없음은 불가가 아닙니다. 119·병원에 최종 확인하세요.</div>'
                phone = str(hospital.get("phone") or "미확인")
                phone_digits = "".join(c for c in phone if c.isdigit() or c in "+-")
                phone_label = escape(phone)
                phone_link = (f'<a href="tel:{escape(phone_digits)}">{phone_label}</a>'
                              if any(c.isdigit() for c in phone) else phone_label)
                name = escape(str(hospital.get("name", "병원명 미확인")))
                beds_label = escape(str(hospital.get("emergency_beds", "미확인")))
                address = escape(str(hospital.get("address", "주소 미확인")))
                route_button = (
                    '<button type="button" class="car-route-button" '
                    f'data-lat="{escape(str(hospital["lat"]), quote=True)}" '
                    f'data-lon="{escape(str(hospital["lon"]), quote=True)}" '
                    f'data-name="{name}" aria-controls="car-route-panel">'
                    '현재 GPS에서 자동차 경로 보기</button>'
                )
                hospital_cards += f"""
                    <details class="hospital-card" style="margin-bottom:12px;">
                        <summary style="cursor:pointer;list-style-position:inside;line-height:1.9;">
                            <strong style="font-size:1.1rem;">🏥 {name}</strong><br>
                            <span>📍 직선거리 <strong>{distance:.1f}km</strong></span> ·
                            <span>🛏️ 가용 병상 <strong>{beds_label}</strong></span><br>
                            <span>☎️ 응급실 전화 <strong>{phone_label}</strong></span><br>
                            {road_html}
                            <span class="muted" style="font-size:.85rem;">상세정보 펼치기 ▾</span>
                        </summary>
                        <div style="margin-top:12px;padding-top:12px;border-top:1px solid #e2e8f0;">
                            <div class="hospital-detail"><strong>주소:</strong> {address}</div>
                            <div class="hospital-detail"><strong>전화:</strong> {phone_link}</div>
                            {route_button}
                            <div class="hospital-detail"><strong>응급실 가용 병상(API 보고값):</strong> {beds_label}</div>
                            <div class="hospital-detail muted">※ 병상 수는 실시간 수용 확약이 아닙니다. 119 또는 병원에 확인하세요.</div>
                            {symptom_support_html}
                            {verification_html}
                            {severe_html}
                            {resource_html}
                            {hira_html}
                        </div>
                    </details>
                """

            hospital_cards = (
                '<div class="location" style="margin-bottom:12px;">'
                '<strong>📍 현재 위치 기준 가까운 응급실 최대 5곳</strong><br>'
                '거리순으로 표시됩니다. 병원을 누르면 자세한 정보를 확인할 수 있습니다. '
                '목록의 거리·시간은 응급실 조회 시 위치 기준이며, 자동차 경로 버튼은 GPS를 새로 확인합니다. '
                '병상은 공공 API 보고값이며 실제 수용 가능 여부는 119·병원에 확인하세요.'
                '</div>' + templates.env.get_template('transport_workspace.html' if chat_ui_enabled(request) else 'car_route_panel.html').render(tts_cloud=neural_enabled()) + hospital_cards
            )

        else:

            hospital_cards = f"""

                <div class="location">

                    <strong>전국 응급의료 API 조회를 완료하지 못했습니다.</strong><br>

                    {hospital_error}<br><br>

                    <strong>설정 방법</strong><br>

                    1. 공공데이터포털에서 ‘국립중앙의료원_전국 응급의료기관 정보 조회 서비스’를 신청합니다.<br>

                    2. 발급받은 인증키를 서버 실행 전 환경변수 <code>EMERGENCY_API_KEY</code>에 설정합니다.<br>

                    3. 서버를 다시 실행한 뒤 현재 위치를 다시 확인합니다.

                </div>

            """

    else:

        hospital_cards = """

            <div class="location">

                전국 응급의료기관을 현재 위치 기준으로 조회하려면 먼저 현재 위치를 확인해주세요.

            </div>

        """



    return HTMLResponse(f"""

        <!DOCTYPE html>

        <html lang="ko">



        <head>

            <meta charset="UTF-8">

            <meta name="viewport" content="width=device-width, initial-scale=1.0">

            <title>현재 위치 및 주변 응급실</title>



            <style>

                * {{

                    box-sizing: border-box;

                }}



                body {{

                    margin: 0;

                    font-family: Arial, sans-serif;

                    background-color: #f2f4f7;

                    min-height: 100vh;

                    padding: 30px 0;

                }}



                .container {{

                    width: 90%;

                    max-width: 500px;

                    margin: auto;

                    background-color: white;

                    padding: 30px;

                    border-radius: 20px;

                }}



                h1 {{

                    text-align: center;

                    margin-bottom: 10px;

                }}



                .description {{

                    text-align: center;

                    color: #666;

                    line-height: 1.5;

                    margin-bottom: 30px;

                }}



                .section-title {{

                    font-size: 19px;

                    font-weight: bold;

                    margin-top: 25px;

                    margin-bottom: 12px;

                }}



                .info {{

                    background-color: #f5f5f5;

                    padding: 15px;

                    margin-bottom: 10px;

                    border-radius: 10px;

                }}



                .label {{

                    font-weight: bold;

                    margin-bottom: 5px;

                }}



                .value {{

                    font-size: 17px;

                    word-break: break-word;

                }}



                .emergency {{

                    padding: 16px;

                    margin-bottom: 10px;

                    border-radius: 10px;

                }}



                .normal {{

                    background-color: #e3f2fd;

                    border-left: 5px solid #1976d2;

                }}



                .warning {{

                    background-color: #ffebee;

                    border-left: 5px solid #d32f2f;

                }}



                .symptom {{

                    background-color: #fff8e1;

                    border-left: 5px solid #f9a825;

                    padding: 16px;

                    margin-bottom: 10px;

                    border-radius: 10px;

                }}



                .location {{

                    background-color: #f5f5f5;

                    padding: 18px;

                    border-radius: 10px;

                    color: #555;

                    line-height: 1.6;

                }}



                .location-button {{

                    display: block;

                    width: 100%;

                    padding: 15px;

                    margin-top: 10px;

                    background-color: #1976d2;

                    color: white;

                    border: none;

                    border-radius: 10px;

                    font-size: 16px;

                    font-weight: bold;

                    cursor: pointer;

                }}



                .location-button:disabled {{

                    background-color: #999;

                    cursor: not-allowed;

                }}



                .hospital-card {{

                    border: 1px solid #e0e0e0;

                    border-radius: 12px;

                    padding: 16px;

                    margin-bottom: 12px;

                    background: #fff;

                }}



                .hospital-name {{

                    font-size: 18px;

                    font-weight: bold;

                    margin-bottom: 8px;

                    color: #1f2937;

                }}



                .hospital-tag {{

                    display: inline-block;

                    background: #e3f2fd;

                    color: #1565c0;

                    padding: 4px 8px;

                    border-radius: 6px;

                    font-size: 12px;

                    margin-bottom: 10px;

                }}



                .hospital-detail {{

                    font-size: 14px;

                    line-height: 1.6;

                    margin-top: 5px;

                    color: #333;

                    word-break: keep-all;

                }}



                .hospital-address {{

                    margin-top: 8px;

                    color: #666;

                    font-size: 12px;

                    line-height: 1.5;

                }}



                .muted {{

                    color: #777;

                    font-size: 12px;

                }}



                .button {{

                    display: block;

                    width: 100%;

                    padding: 18px;

                    margin-top: 25px;

                    background-color: #d32f2f;

                    color: white;

                    text-decoration: none;

                    text-align: center;

                    border-radius: 12px;

                    font-size: 18px;

                    font-weight: bold;

                }}



                .home {{

                    display: block;

                    text-align: center;

                    margin-top: 20px;

                    color: #555;

                    text-decoration: none;

                }}

            </style>

        </head>



        <body>



            <div class="container">



                <h1>📍 현재 위치 및 주변 응급실</h1>

                <div class="description">
                    위치를 확인하고 주변 응급의료기관의 정보를 조회합니다.<br>
                    응급환자 정보는 이전 페이지에서 확인할 수 있습니다.
                </div>

                <div class="section-title">

                    📍 현재 위치

                </div>



                <div class="location" id="location-box">

                    {("<strong>위치 확인 완료</strong><br>위도: " + str(latitude) + "<br>경도: " + str(longitude) + "<br>정확도: " + ("약 " + str(round(accuracy, 1)) + "m" if accuracy is not None else "미확인")) if latitude is not None and longitude is not None else "아래 버튼을 눌러 위치 권한을 요청하고 주변 응급실을 조회하세요."}

                </div>



                <div class="section-title">

                    🏥 주변 응급실

                </div>



                <div class="location" style="margin-bottom: 12px;">

                    현재 위치가 전국 어느 지역이든 해당 지역의 응급의료기관을 공공 API로 조회합니다.<br>

                    표시되는 가용 병상은 응급실 기준이며, 세부 진료과는 기관별 기본정보 API에서 조회하며, 정보가 제공되지 않는 경우 미확인으로 표시합니다.

                </div>



                {hospital_cards}



                <script>



                    function getCurrentLocation() {{



                        const locationBox = document.getElementById("location-box");

                        if (!window.isSecureContext) {{
                            locationBox.textContent = "GPS 사용에는 신뢰되는 HTTPS 접속이 필요합니다. 설치·권한 확인 화면에서 연결을 확인해 주세요.";
                            return;
                        }}



                        if (!navigator.geolocation) {{



                            locationBox.innerHTML =

                                "이 브라우저에서는 위치 정보를 사용할 수 없습니다.";



                            return;

                        }}





                        locationBox.innerHTML =

                            "현재 위치를 확인하고 있습니다.<br>잠시만 기다려주세요.";



                        navigator.geolocation.getCurrentPosition(



                            function(position) {{



                                const latitude = position.coords.latitude;

                                const longitude = position.coords.longitude;

                                const accuracy = position.coords.accuracy;



                                const url = new URL(window.location.href);



                                url.searchParams.set("latitude", latitude);

                                url.searchParams.set("longitude", longitude);

                                url.searchParams.set("accuracy", accuracy);



                                window.location.href = url.toString();

                            }},



                            function(error) {{



                                let message = "현재 위치를 확인할 수 없습니다.";



                                if (error.code === 1) {{

                                    message = "위치 정보 사용 권한이 거부되었습니다. 브라우저의 위치 권한을 허용해주세요.";

                                }} else if (error.code === 2) {{

                                    message = "현재 위치를 확인할 수 없습니다.";

                                }} else if (error.code === 3) {{

                                    message = "위치 확인 시간이 초과되었습니다. 다시 시도해주세요.";

                                }}



                                locationBox.innerHTML = message;

                                locationBox.innerHTML += "<br>위치 권한을 확인한 뒤 페이지를 새로고침해주세요.";

                            }},



                            {{

                                enableHighAccuracy: true,

                                timeout: 10000,

                                maximumAge: 0

                            }}

                        );

                    }}



                    // 위치 권한은 사용자가 아래 버튼을 누른 경우에만 요청합니다.

                </script>
                <button type="button" class="location-button" onclick="getCurrentLocation()">현재 위치 확인 · 다시 시도</button>



                <a

                    href="/"

                    class="button"

                >

                    🏠 처음 화면으로

                </a>



                <a

                    href="/emergency"

                    class="home"

                >

                    응급상황 다시 시작

                </a>



            </div>



        </body>



        </html>

    """)
