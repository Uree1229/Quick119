"""Explicit loopback demo only. Straight test lines are never real road guidance."""
import math
import os

from fastapi import HTTPException

from seoul_demo import load_catalog


def demo_route(request, payload):
    if os.getenv('QUICK119_SYNTHETIC_ROUTES') != '1':
        return None
    from team_chat_ui import local_only
    local_only(request)
    catalog = load_catalog()
    start = catalog["origin"]
    if abs(payload.origin.lat - start["lat"]) > 1e-7 or abs(payload.origin.lon - start["lon"]) > 1e-7:
        raise HTTPException(400, '서울 시연 경로의 출발지는 동작구 흑석동입니다.')
    if not any(abs(payload.destination.lat - h["lat"]) < 1e-7
               and abs(payload.destination.lon - h["lon"]) < 1e-7 for h in catalog["hospitals"]):
        raise HTTPException(400, '서울 시연 목록에 있는 병원을 선택해 주세요.')
    a, b = payload.origin, payload.destination
    radians = math.pi / 180
    h = math.sin((b.lat-a.lat)*radians/2)**2 + math.cos(a.lat*radians)*math.cos(b.lat*radians)*math.sin((b.lon-a.lon)*radians/2)**2
    # Same local travel assumptions as the team runtime's candidate list and ETA.
    distance = 6371000 * 2 * math.asin(math.sqrt(min(1, h))) * 1.3
    return {'code': 'ok', 'status': '가상 직선 경로 · 실제 길안내 아님', 'provider': 'synthetic',
            'distance_m': round(distance), 'duration_sec': max(1, math.ceil(distance/1000/40*60))*60, 'cached': False,
            'coordinates': [[a.lon, a.lat], [b.lon, b.lat]]}
