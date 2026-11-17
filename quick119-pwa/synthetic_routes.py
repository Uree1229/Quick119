"""Explicit loopback demo only. Straight test lines are never real road guidance."""
import math
import os

from fastapi import HTTPException


def demo_route(request, payload):
    if os.getenv('QUICK119_SYNTHETIC_ROUTES') != '1':
        return None
    from team_chat_ui import local_only
    local_only(request)
    # Coordinates of the pinned team's ten synthetic fixtures (server/synthetic.py).
    offsets = ((.012, -.020), (.030, .035), (-.010, -.060), (-.025, .010), (-.050, .020),
               (.060, -.005), (.005, .050), (.180, .150), (.040, -.110), (-.220, -.180))
    if not any(abs(payload.destination.lat - (35.1595 + lat)) < 1e-7
               and abs(payload.destination.lon - (126.8526 + lon)) < 1e-7 for lat, lon in offsets):
        raise HTTPException(400, '가상 경로 모드에서는 팀의 가상 병원 좌표만 사용할 수 있습니다.')
    a, b = payload.origin, payload.destination
    radians = math.pi / 180
    h = math.sin((b.lat-a.lat)*radians/2)**2 + math.cos(a.lat*radians)*math.cos(b.lat*radians)*math.sin((b.lon-a.lon)*radians/2)**2
    distance = 6371000 * 2 * math.asin(math.sqrt(min(1, h)))
    return {'code': 'ok', 'status': '가상 직선 경로 · 실제 길안내 아님', 'provider': 'synthetic',
            'distance_m': round(distance), 'duration_sec': round(distance/10), 'cached': False,
            'coordinates': [[a.lon, a.lat], [b.lon, b.lat]]}
