"""Opt-in, loopback-only gateway to the team's synthetic runtime.

Uses the team's existing paths; never opens a DB or loads credentials.
This preparation stage is deliberately unavailable in production or over LAN.
"""
import json
import os
import re
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from intake_chat import (crew_conversation, crew_message_response,
                         confirmed_hospital_conversation)

router = APIRouter()
MAX_BYTES = 1_048_576
REVISION = "team-runtime-20261030-v1"


def _base_url():
    if (os.getenv("QUICK119_TEAM_CHAT_ENABLED") != "1"
            or os.getenv("QUICK119_ENV", "development") not in {"development", "test"}):
        raise HTTPException(503, "가상 채팅 서버 연결이 비활성화되어 있습니다.")
    raw = os.getenv("QUICK119_TEAM_CHAT_URL", "http://127.0.0.1:18033")
    try:
        url = urlsplit(raw)
        valid = (url.scheme == "http" and url.hostname == "127.0.0.1"
                 and url.port is not None and 1 <= url.port <= 65535
                 and url.username is None and url.password is None
                 and url.path in {"", "/"} and not url.query and not url.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(503, "가상 채팅 서버의 loopback 주소 설정을 확인하세요.")
    return f"http://127.0.0.1:{url.port}"


async def _json_response(client, method, url, **kwargs):
    async with client.stream(method, url, **kwargs) as response:
        if response.status_code >= 500 or not 200 <= response.status_code < 500 or response.is_redirect:
            raise HTTPException(502, "가상 채팅 서버가 정상 응답하지 않았습니다.")
        if response.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(502, "가상 채팅 서버의 응답 형식이 올바르지 않습니다.")
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > MAX_BYTES:
                raise HTTPException(502, "가상 채팅 서버 응답이 너무 큽니다.")
        try:
            return response.status_code, json.loads(content)
        except (ValueError, UnicodeError):
            raise HTTPException(502, "가상 채팅 서버의 JSON 응답을 확인하세요.") from None


async def forward(request: Request):
    base = _base_url()
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "현재 연결 검증은 로컬 PC의 가상 데이터만 허용합니다.")
    origin = request.headers.get("origin")
    if (request.headers.get("sec-fetch-site") == "cross-site"
            or (origin and origin != f"{request.url.scheme}://{request.url.netloc}")):
        raise HTTPException(403, "다른 출처의 채팅 요청은 허용하지 않습니다.")
    for key, value in request.path_params.items():
        if key == "pid" and not re.fullmatch(r"Q119-TEST-[A-Za-z0-9-]{1,80}", value):
            raise HTTPException(403, "Q119-TEST- 가상 이송 건만 연결할 수 있습니다.")
        if key == "hid" and value not in {f"H{i:02d}" for i in range(1, 11)}:
            raise HTTPException(403, "가상 병원 H01~H10만 연결할 수 있습니다.")
        if key in {"iid", "eid", "rid", "aid"} and not re.fullmatch(r"[1-9][0-9]{0,18}", value):
            raise HTTPException(400, "기록 ID가 올바르지 않습니다.")
    if len(request.url.query) > 2048:
        raise HTTPException(400, "조회 조건이 너무 깁니다.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BYTES:
            raise HTTPException(413, "요청이 너무 큽니다.")
    if request.method in {"POST", "PATCH", "DELETE"}:
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            raise HTTPException(400, "JSON 객체가 필요합니다.") from None
        if request.url.path == "/api/patients":
            if (not re.fullmatch(r"Q119-TEST-[A-Za-z0-9-]{1,80}", str(payload.get("id", "")))
                    or not isinstance(payload.get("extra"), dict)
                    or payload["extra"].get("synthetic_fixture") is not True):
                raise HTTPException(403, "가상 이송 ID와 synthetic_fixture=true 표시가 필요합니다.")
    try:
        # No .env, proxy settings, cookies, Authorization, or caller-supplied host forwarding.
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                     timeout=httpx.Timeout(5.0, connect=2.0)) as client:
            status, info = await _json_response(client, "GET", base + "/api/team-runtime")
            if (status != 200 or not isinstance(info, dict) or info.get("synthetic") is not True
                    or info.get("revision") != REVISION or info.get("travel_provider") != "synthetic"
                    or info.get("external_hospital_messages") is not False
                    or not isinstance(info.get("database"), dict)
                    or info["database"].get("valid") is not True):
                raise HTTPException(503, "팀 서버의 가상 데이터 전용 실행을 확인할 수 없습니다.")
            if request.url.path == "/api/team-runtime":
                result = info
            else:
                status, result = await _json_response(
                    client, request.method, base + request.url.path,
                    params=request.query_params.multi_items(), content=bytes(body) if body else None,
                    headers={"Content-Type": "application/json", "Accept": "application/json"})
                if 200 <= status < 300 and isinstance(result, dict):
                    pid = request.path_params.get('pid')
                    hid = request.path_params.get('hid')
                    if pid and request.method == 'GET' and request.url.path.endswith('/conversation'):
                        if hid is None:
                            result = dict(crew_conversation(result), intake_commands=True)
                        elif (result.get('active_hospital_session') or {}).get('hospital_id') != hid:
                            raise HTTPException(403, '현재 이송 대화에 참여 중인 병원만 조회할 수 있습니다.')
                        else:
                            # The hospital GET above is the authorization gate.
                            # Read global streams only for the current confirmed
                            # participant, then filter them before returning.
                            crew_status, crew_page = await _json_response(
                                client, 'GET', base + f'/api/patients/{pid}/conversation',
                                params=request.query_params.multi_items(),
                                headers={'Accept': 'application/json'})
                            if crew_status != 200 or not isinstance(crew_page, dict):
                                raise HTTPException(502, '현장 보고 기록을 조회하지 못했습니다.')
                            result = confirmed_hospital_conversation(result, crew_page, hid)
                    elif pid and hid is None and request.method == 'POST' and request.url.path.endswith('/messages'):
                        result = crew_message_response(result)
    except httpx.TimeoutException:
        raise HTTPException(504, "가상 채팅 서버 응답 시간이 초과됐습니다.") from None
    except httpx.HTTPError:
        raise HTTPException(502, "가상 채팅 서버에 연결하지 못했습니다.") from None
    return JSONResponse(result, status_code=status, headers={"Cache-Control": "no-store"})


# Explicit allowlist: these are existing team contracts, not new business endpoints.
for method, path in (
    ("GET", "/api/team-runtime"),
    ("GET", "/api/system/status"),
    ("GET", "/api/hospitals"),
    ("GET", "/api/hospitals/{hid}"),
    ("GET", "/api/hospitals/{hid}/inquiries"),
    ("GET", "/api/hospitals/{hid}/incoming"),
    ("GET", "/api/hospitals/{hid}/transport-change-reviews"),
    ("GET", "/api/hospitals/{hid}/data-provenance"),
    ("GET", "/api/hospitals/{hid}/call-records"),
    ("POST", "/api/hospitals/{hid}/restrictions"),
    ("DELETE", "/api/hospitals/{hid}/restrictions/{rid}"),
    ("GET", "/api/hospitals/{hid}/external-arrivals"),
    ("POST", "/api/hospitals/{hid}/external-arrivals"),
    ("POST", "/api/hospitals/{hid}/external-arrivals/{eid}/discharge"),
    ("POST", "/api/patients"),
    ("GET", "/api/transport/patients"),
    ("GET", "/api/patients/{pid}"),
    ("DELETE", "/api/patients/{pid}"),
    ("PATCH", "/api/patients/{pid}"),
    ("GET", "/api/patients/{pid}/intake-draft"),
    ("POST", "/api/patients/{pid}/intake-draft"),
    ("POST", "/api/patients/{pid}/intake-draft/confirm"),
    ("POST", "/api/patients/{pid}/intake-draft/suggestion"),
    ("GET", "/api/patients/{pid}/candidates"),
    ("POST", "/api/patients/{pid}/candidates"),
    ("GET", "/api/patients/{pid}/input-review"),
    ("GET", "/api/patients/{pid}/transport-review"),
    ("POST", "/api/patients/{pid}/inquiries"),
    ("POST", "/api/inquiries/{iid}/respond"),
    ("POST", "/api/patients/{pid}/confirm"),
    ("POST", "/api/patients/{pid}/change-destination"),
    ("POST", "/api/patients/{pid}/depart"),
    ("GET", "/api/patients/{pid}/conversation"),
    ("POST", "/api/patients/{pid}/messages"),
    ("POST", "/api/hospitals/{hid}/patients/{pid}/decision"),
    ("POST", "/api/hospitals/{hid}/patients/{pid}/arrive"),
    ("POST", "/api/hospitals/{hid}/patients/{pid}/discharge"),
    ("POST", "/api/hospitals/{hid}/patients/{pid}/call-records"),
    ("POST", "/api/hospitals/{hid}/patients/{pid}/call-records/{rid}/confirmation"),
    ("GET", "/api/hospitals/{hid}/patients/{pid}/work-activities"),
    ("GET", "/api/hospitals/{hid}/patients/{pid}/work-activities/summary"),
    ("POST", "/api/hospitals/{hid}/patients/{pid}/work-activities"),
    ("POST", "/api/hospitals/{hid}/patients/{pid}/work-activities/{aid}/stop"),
    ("GET", "/api/hospitals/{hid}/patients/{pid}/conversation"),
    ("GET", "/api/hospitals/{hid}/patients/{pid}/transport-location"),
    ("POST", "/api/hospitals/{hid}/patients/{pid}/messages"),
    ("GET", "/api/hospitals/{hid}/patients/{pid}/important-events"),
    ("POST", "/api/hospitals/{hid}/patients/{pid}/important-events/{eid}/delivered"),
    ("POST", "/api/hospitals/{hid}/patients/{pid}/important-events/{eid}/acknowledge"),
):
    router.add_api_route(path, forward, methods=[method], include_in_schema=False)
