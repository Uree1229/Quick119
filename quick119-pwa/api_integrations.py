"""캡스톤 응급의료 API 연동 모듈. 키가 없어도 앱은 실행됩니다."""
import os
import json
import urllib.request
import urllib.parse
import urllib.error
import socket
import math
import hashlib
import copy
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from settings import require_external_api, external_api_enabled, kakao_rest_api_key


def _request(url, params=None, headers=None, method='GET', body=None):
    require_external_api()
    if params:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=8) as response:
        raw = response.read()
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        root = ET.fromstring(raw)
        code = root.findtext('.//resultCode')
        if code and code not in ('00', '0'):
            raise ValueError('API 응답 오류: ' + str(code))
        return [{e.tag: (e.text or '').strip() for e in item} for item in root.findall('.//item')]


def _key(name):
    if name == 'KAKAO_REST_API_KEY':
        return kakao_rest_api_key()
    if not external_api_enabled():
        return ''
    val = os.getenv(name, '').strip()
    return val if val and not val.startswith('발급') else ''


def _items(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    body = data.get('response', {}).get('body', {}) if isinstance(data.get('response'), dict) else data.get('body', {})
    items = body.get('items', {}) if isinstance(body, dict) else {}
    if isinstance(items, dict):
        return items.get('item', []) if isinstance(items.get('item', []), list) else [items['item']] if items.get('item') else []
    return items if isinstance(items, list) else []


def _hira_request(url_env, **params):
    key = _key('HIRA_API_KEY')
    endpoint = os.getenv(url_env, '').strip()
    if not key or not endpoint:
        return [], '심평원 키 또는 상세기능 URL 설정 대기'
    try:
        data = _request(endpoint, {'ServiceKey': key, 'numOfRows': 100, 'pageNo': 1, **params})
        return _items(data), None
    except Exception as e:
        return [], f'심평원 조회 실패: {type(e).__name__}'


def hira_hospital_info(name):
    """공공데이터포털에서 복사한 실제 상세기능 URL을 .env에 지정해야 작동."""
    if not _key('HIRA_API_KEY'):
        return {'status': 'API 키 발급 대기'}
    rows, err = _hira_request('HIRA_HOSPITAL_URL', yadmNm=name)
    if err:
        return {'status': err}
    matches = [r for r in rows if r.get('yadmNm', '').replace(' ', '') == name.replace(' ', '')]
    if len(matches) != 1:
        return {'status': '병원 식별자 매칭 확인 필요'}
    return {'status': '조회됨', 'ykiho': matches[0].get('ykiho', ''), 'details': matches[0]}


def hira_details(ykiho):
    """진료과·인력·시설·장비 각각의 상세기능 URL과 응답 형식 확인 후 연결."""
    if not ykiho:
        return {'status': '병원 식별자 미확인'}
    result = {}
    for label, env in [('진료과·인력', 'HIRA_DEPARTMENT_URL'), ('시설·장비', 'HIRA_EQUIPMENT_URL')]:
        rows, err = _hira_request(env, ykiho=ykiho)
        result[label] = {'status': err or '조회됨', 'items': rows[:20]}
    return result


KAKAO_NAVI_BASE_URL = 'https://apis-navi.kakaomobility.com'
_KAKAO_CACHE = {}
_KAKAO_CACHE_SECONDS = 30
_KAKAO_CACHE_LIMIT = 128


def _coordinate(lat, lon):
    lat, lon = float(lat), float(lon)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('Invalid coordinates')
    return lat, lon


def _route_summary(route, geometry=False):
    if not isinstance(route, dict):
        raise ValueError('Invalid route')
    code = route.get('result_code')
    if type(code) is not int:
        raise ValueError('Invalid result code')
    if code != 0:
        return {'status': '경로 탐색 결과 없음', 'code': 'no_route', 'result_code': code}
    summary = route.get('summary', {})
    distance, duration = summary.get('distance'), summary.get('duration')
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in (distance, duration)):
        raise ValueError('Invalid summary')
    result = {'status': '조회됨', 'code': 'ok', 'result_code': 0,
              'distance_m': distance, 'duration_sec': duration,
              'distance_km': round(distance / 1000, 1),
              'duration_min': math.ceil(duration / 60), 'vehicle_basis': '일반 자동차 기준'}
    if geometry:
        coordinates = []
        for section in route.get('sections', []):
            for road in section.get('roads', []):
                vertices = road.get('vertexes', [])
                if len(vertices) % 2:
                    raise ValueError('Invalid geometry')
                for i in range(0, len(vertices), 2):
                    lat, lon = _coordinate(vertices[i + 1], vertices[i])
                    point = [lon, lat]
                    if not coordinates or coordinates[-1] != point:
                        coordinates.append(point)
        result['coordinates'] = coordinates  # [longitude, latitude], ready for a map in stage 2
    return result


def _kakao_call(path, params=None, body=None, parser=None):
    if not external_api_enabled():
        return {'status': '외부 API 연동 대기', 'code': 'disabled'}
    key = kakao_rest_api_key()
    if not key:
        return {'status': '카카오 API 키 발급 대기', 'code': 'missing_key'}
    cache_key = (path, json.dumps(params or body, sort_keys=True), hashlib.sha256(key.encode()).digest())
    # A bounded, short-lived memory cache; never persisted to the DB or PWA cache.
    # Coalesce identical requests while allowing different routes to run concurrently.
    with _KAKAO_REQUEST_LOCKS[hash(cache_key) % len(_KAKAO_REQUEST_LOCKS)]:
        with _KAKAO_CACHE_LOCK:
            now = time.monotonic()
            for expired in [k for k, (until, _) in _KAKAO_CACHE.items() if until <= now]:
                del _KAKAO_CACHE[expired]
            if cache_key in _KAKAO_CACHE:
                return {**copy.deepcopy(_KAKAO_CACHE[cache_key][1]), 'cached': True}
        try:
            data = _request(KAKAO_NAVI_BASE_URL + path, params,
                            {'Authorization': 'KakaoAK ' + key, 'Content-Type': 'application/json'},
                            method='POST' if body is not None else 'GET', body=body)
            routes = data.get('routes')
            if not isinstance(routes, list) or not routes:
                raise ValueError('Invalid routes')
            result = parser(routes)
            result['upstream_http_status'] = 200
        except urllib.error.HTTPError as exc:
            http_status = exc.code
            exc.close()
            code = {401: 'authentication_failed', 403: 'permission_denied', 429: 'quota_exceeded'}.get(http_status, 'upstream_error')
            result = {'status': '카카오 길찾기 HTTP 오류', 'code': code, 'upstream_http_status': http_status}
        except (TimeoutError, socket.timeout):
            result = {'status': '카카오 길찾기 응답 시간 초과', 'code': 'timeout'}
        except urllib.error.URLError as exc:
            code = 'timeout' if isinstance(exc.reason, (TimeoutError, socket.timeout)) else 'network_error'
            result = {'status': '카카오 길찾기 연결 실패', 'code': code}
        except (ValueError, TypeError, AttributeError, KeyError, ET.ParseError):
            result = {'status': '카카오 길찾기 응답 형식 오류', 'code': 'invalid_response'}
        result['cached'] = False
        ttl = _KAKAO_CACHE_SECONDS if result['code'] == 'ok' else 5
        with _KAKAO_CACHE_LOCK:
            if len(_KAKAO_CACHE) >= _KAKAO_CACHE_LIMIT:
                del _KAKAO_CACHE[next(iter(_KAKAO_CACHE))]
            _KAKAO_CACHE[cache_key] = (time.monotonic() + ttl, copy.deepcopy(result))
        return result


def kakao_route(origin_lat, origin_lon, dest_lat, dest_lon, *, geometry=False):
    origin_lat, origin_lon = _coordinate(origin_lat, origin_lon)
    dest_lat, dest_lon = _coordinate(dest_lat, dest_lon)
    return _kakao_call('/v1/directions', params={
        'origin': f'{origin_lon},{origin_lat}', 'destination': f'{dest_lon},{dest_lat}',
        'priority': 'TIME', 'summary': 'false' if geometry else 'true',
        'alternatives': 'false',
    }, parser=lambda routes: _route_summary(routes[0], geometry))


def kakao_routes(origin_lat, origin_lon, destinations):
    """One multi-destination request for up to five ER coordinates (10 km API radius)."""
    lat, lon = _coordinate(origin_lat, origin_lon)
    if not 1 <= len(destinations) <= 5:
        raise ValueError('Expected 1 to 5 destinations')
    points = []
    keys = set()
    for destination in destinations:
        dlat, dlon = _coordinate(destination['lat'], destination['lon'])
        key = str(destination['key'])
        if key in keys:
            raise ValueError('Duplicate destination key')
        keys.add(key)
        points.append({'key': key, 'x': dlon, 'y': dlat})

    def parse(routes):
        by_key = {}
        for route in routes:
            key = route.get('key')
            if key not in keys or key in by_key:
                raise ValueError('Unexpected destination key')
            by_key[key] = _route_summary(route)
        return {'status': '조회됨', 'code': 'ok', 'radius_m': 10000,
                'routes': [{'key': p['key'], **by_key.get(p['key'], {
                    'status': '목적지 응답 없음', 'code': 'missing_route'})} for p in points]}

    return _kakao_call('/v1/destinations/directions', body={
        'origin': {'x': lon, 'y': lat}, 'destinations': points,
        'radius': 10000, 'priority': 'TIME',
    }, parser=parse)


def enrich_hospitals(hospitals, lat, lon):
    """기존 병원 검색 결과를 유지하며 도로거리·시간 및 HIRA 상태를 덧붙임."""
    for hospital in hospitals:
        hospital['road'] = {'status': '연동 대기'}
        hospital['hira'] = {'status': '연동 대기'}
    if _key('KAKAO_REST_API_KEY'):
        destinations = []
        for index, hospital in enumerate(hospitals[:5]):
            try:
                dlat, dlon = _coordinate(hospital['lat'], hospital['lon'])
                a = (math.sin(math.radians(dlat - lat) / 2) ** 2
                     + math.cos(math.radians(lat)) * math.cos(math.radians(dlat))
                     * math.sin(math.radians(dlon - lon) / 2) ** 2)
                distance = 6371000 * 2 * math.asin(math.sqrt(min(1, a)))
                if distance > 10000:
                    hospital['road'] = {'code': 'outside_radius', 'status': '10km 밖 · 자동차 경로 버튼으로 개별 조회'}
                else:
                    destinations.append({'key': str(index), 'lat': dlat, 'lon': dlon})
            except (ValueError, TypeError, KeyError):
                hospital['road'] = {'code': 'invalid_coordinates', 'status': '병원 좌표 확인 필요'}
        if destinations:
            result = kakao_routes(lat, lon, destinations)
            routes = {route['key']: route for route in result.get('routes', [])}
            for destination in destinations:
                hospitals[int(destination['key'])]['road'] = routes.get(destination['key'], {
                    'code': result.get('code', 'missing_route'),
                    'status': result.get('status', '경로 확인 불가')})
    # HIRA 병원명만으로 자동 병합하면 동명이원 오매칭 위험이 있으므로 별도 검증 후 연결.
    return hospitals


# ===== HIRA 진료과목 API 2.8 (실제 사용자 테스트 성공) =====
import re
import time
import threading

_KAKAO_CACHE_LOCK = threading.Lock()
_KAKAO_REQUEST_LOCKS = [threading.Lock() for _ in range(64)]

_HIRA_CACHE = {}
_HIRA_CACHE_LOCK = threading.Lock()
_HIRA_CACHE_SECONDS = 1800
_HIRA_HOSP_URL = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
_HIRA_DEPT_URL = "https://apis.data.go.kr/B551182/MadmDtlInfoService2.8/getDgsbjtInfo2.8"
_HIRA_FACILITY_URL = "https://apis.data.go.kr/B551182/MadmDtlInfoService2.8/getEqpInfo2.8"
_HIRA_EQUIPMENT_URL = "https://apis.data.go.kr/B551182/MadmDtlInfoService2.8/getMedOftInfo2.8"


def _hira_xml(url, params):
    require_external_api()
    key = _key("HIRA_API_KEY")
    if not key:
        raise ValueError("심평원 API 키 미설정")
    query = urllib.parse.urlencode({"serviceKey": key, "pageNo": 1, "numOfRows": 100, "_type": "xml", **params})
    request = urllib.request.Request(url + "?" + query, headers={"User-Agent": "EmergencyPatientSupportCapstone/1.0"})
    with urllib.request.urlopen(request, timeout=7) as response:
        root = ET.fromstring(response.read())
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in ("00", "0"):
        raise ValueError("심평원 응답 코드 " + (code or "없음"))
    return [{child.tag: (child.text or "").strip() for child in item} for item in root.findall(".//items/item")]


def _normalize_hospital_name(value):
    # 법인명/지점명이 달라질 수 있으므로 임의로 삭제하지 않습니다.
    return re.sub(r"[^0-9a-z가-힣]", "", (value or "").lower())


def _address_compatible(a, b):
    # 행정구역 + 도로명(가능하면 건물번호)까지 비교; 불충분하면 매칭 보류
    def tokens(address):
        address = re.sub(r"\([^)]*\)", "", address or "")
        address = address.replace(",", " ")
        return [re.sub(r"[^0-9a-z가-힣]", "", x.lower()) for x in address.split() if x.strip()]
    aa, bb = tokens(a), tokens(b)
    if len(aa) < 4 or len(bb) < 4:
        return False
    # 시/도 표기 차이 허용, 시군구와 도로명+건물번호 일치 필수
    district_a = next((t for t in aa if t.endswith(("구", "군", "시")) and t not in ("성남시",)), None)
    district_b = next((t for t in bb if t.endswith(("구", "군", "시")) and t not in ("성남시",)), None)
    if district_a and district_b and district_a != district_b:
        return False
    road_a = next(((aa[i], aa[i+1]) for i in range(len(aa)-1) if aa[i].endswith(("로", "길")) and aa[i+1].isdigit()), None)
    road_b = next(((bb[i], bb[i+1]) for i in range(len(bb)-1) if bb[i].endswith(("로", "길")) and bb[i+1].isdigit()), None)
    return road_a is not None and road_a == road_b


def _hira_search_terms(name):
    """Search full name first, then conservative shorter variants."""
    clean = _normalize_hospital_name(name)
    terms = [name]
    # Legal entity prefixes are frequently omitted or spaced differently in HIRA.
    for marker in ("의료재단", "의료법인", "재단법인"):
        if marker in clean:
            suffix = clean.split(marker, 1)[-1]
            if len(suffix) >= 4:
                terms.append(suffix)
    if "성남정병원" in clean:
        terms.append("성남정병원")
    # Avoid querying broad one/two-syllable fragments.
    return list(dict.fromkeys(t for t in terms if len(_normalize_hospital_name(t)) >= 4))


def _name_compatible(a, b):
    aa, bb = _normalize_hospital_name(a), _normalize_hospital_name(b)
    if aa == bb:
        return True
    # Only permit meaningful suffix equivalence when both names identify a hospital.
    shorter, longer = sorted((aa, bb), key=len)
    return len(shorter) >= 5 and shorter.endswith("병원") and longer.endswith(shorter)


def _hira_department_for_hospital(hospital):
    name, address = hospital.get("name", ""), hospital.get("address", "")
    if not name or not address or address == "주소 미확인":
        return {"status": "병원명 또는 주소 미확인"}
    cache_key = (_normalize_hospital_name(name), address)
    with _HIRA_CACHE_LOCK:
        cached = _HIRA_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < _HIRA_CACHE_SECONDS:
            return cached[1]
    try:
        candidates = {}
        for term in _hira_search_terms(name):
            rows = _hira_xml(_HIRA_HOSP_URL, {"yadmNm": term})
            for row in rows:
                if not row.get("ykiho"):
                    continue
                if not _name_compatible(name, row.get("yadmNm", "")):
                    continue
                if not _address_compatible(address, row.get("addr", "")):
                    continue
                candidates[row["ykiho"]] = row
            # An unambiguous match with verified address needs no broader query.
            if len(candidates) == 1:
                break
        if len(candidates) != 1:
            result = {"status": "병원 식별자 매칭 확인 필요"}
        else:
            ykiho = next(iter(candidates))
            # 진료과, 시설, 장비는 독립적으로 조회: 하나가 실패해도 나머지는 표시합니다.
            result = {"status": "조회됨", "departments": [],
                      "facility": {"status": "미확인", "items": {}},
                      "equipment": {"status": "미확인", "items": []}}
            try:
                details = _hira_xml(_HIRA_DEPT_URL, {"ykiho": ykiho})
                for d in details:
                    subject = d.get("dgsbjtCdNm", "").strip()
                    if not subject:
                        continue
                    try:
                        specialists = int(d.get("dgsbjtPrSdrCnt", ""))
                    except (ValueError, TypeError):
                        specialists = None
                    result["departments"].append({"name": subject, "specialists": specialists})
                if not result["departments"]:
                    result["department_status"] = "진료과목 응답 없음"
            except Exception as exc:
                result["department_status"] = "진료과목 조회 실패 (" + type(exc).__name__ + ")"
            try:
                facilities = _hira_xml(_HIRA_FACILITY_URL, {"ykiho": ykiho})
                if facilities:
                    # 확인된 공식 응답 필드만 사용합니다. 보유량은 실시간 가용량이 아닙니다.
                    field_names = {
                        "permSbdCnt": "허가 병상", "stdSickbdCnt": "일반 입원병상",
                        "soprmCnt": "수술실", "partumCnt": "분만실",
                        "ptrmCnt": "물리치료실",
                    }
                    parsed = {}
                    for field, label in field_names.items():
                        raw = facilities[0].get(field, "").strip()
                        if raw.isdigit():
                            parsed[label] = int(raw)
                    result["facility"] = {"status": "조회됨" if parsed else "표시 가능한 시설 항목 없음", "items": parsed}
                else:
                    result["facility"] = {"status": "시설정보 응답 없음", "items": {}}
            except Exception as exc:
                result["facility"] = {"status": "시설 조회 실패 (" + type(exc).__name__ + ")", "items": {}}
            try:
                equipment = _hira_xml(_HIRA_EQUIPMENT_URL, {"ykiho": ykiho})
                parsed = []
                for item in equipment:
                    label = item.get("oftCdNm", "").strip()
                    raw = item.get("oftCnt", "").strip()
                    if label and raw.isdigit():
                        parsed.append({"name": label, "count": int(raw)})
                result["equipment"] = {"status": "조회됨" if parsed else "장비정보 응답 없음", "items": parsed}
            except Exception as exc:
                result["equipment"] = {"status": "장비 조회 실패 (" + type(exc).__name__ + ")", "items": []}
    except Exception as e:
        result = {"status": "심평원 조회 실패 (" + type(e).__name__ + ")"}
    with _HIRA_CACHE_LOCK:
        _HIRA_CACHE[cache_key] = (time.monotonic(), result)
    return result


def enrich_hospitals_with_hira(hospitals):
    if not _key("HIRA_API_KEY"):
        for hospital in hospitals:
            hospital["hira"] = {"status": "HIRA_API_KEY 미설정"}
        return hospitals
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_hira_department_for_hospital, hospital): hospital for hospital in hospitals}
        for future in as_completed(futures):
            hospital = futures[future]
            try:
                hospital["hira"] = future.result()
            except Exception:
                hospital["hira"] = {"status": "심평원 조회 실패"}
    return hospitals


# V6: 국립중앙의료원 중증질환 수용정보 (병원 식별자 우선 매칭)
def enrich_hospitals_with_severe(hospitals, stage1, stage2):
    """행정구역의 중증질환 정보를 조회해 HPID가 일치하는 병원에만 붙입니다.

    API가 HPID를 제공하지 않는 경우, 병원명이 정확히 일치하고 유일한 경우에만
    보조적으로 매칭합니다. 결과는 병원 수용 확약이 아닙니다.
    """
    for hospital in hospitals:
        hospital['severe'] = {'status': '조회되지 않음', 'items': []}
    key = _key('EMERGENCY_API_KEY')
    if not key or not stage1 or not stage2 or not hospitals:
        return hospitals
    endpoint = ('https://apis.data.go.kr/B552657/ErmctInfoInqireService/'
                'getSrsillDissAceptncPosblInfoInqire')
    rows = []
    try:
        for page in range(1, 6):
            chunk = _request(endpoint, {
                'serviceKey': key, 'STAGE1': stage1, 'STAGE2': stage2,
                'pageNo': page, 'numOfRows': 100
            })
            if not isinstance(chunk, list):
                break
            rows.extend(chunk)
            if len(chunk) < 100:
                break
    except Exception:
        for hospital in hospitals:
            hospital['severe']['status'] = '중증질환 API 조회 실패'
        return hospitals

    def norm(value):
        return ''.join(str(value or '').split()).lower()

    for hospital in hospitals:
        hid = norm(hospital.get('hpid'))
        matches = [r for r in rows if hid and norm(r.get('hpid') or r.get('HPID')) == hid]
        if not matches and not any(norm(r.get('hpid') or r.get('HPID')) for r in rows):
            name = norm(hospital.get('name'))
            matches = [r for r in rows if name and norm(r.get('dutyName')) == name]
        if len(matches) != 1:
            hospital['severe']['status'] = '일치 병원 없음' if not matches else '병원 매칭 중복'
            continue
        row = matches[0]
        values = []
        for i in range(1, 29):
            code = f'MKioskTy{i}'
            value = (row.get(code) or '').strip()
            msg = (row.get(code + 'Msg') or '').strip()
            if value or msg:
                values.append({'code': code, 'value': value or '정보미제공', 'message': msg})
        hospital['severe'] = {'status': '조회됨' if values else '제공 항목 없음', 'items': values}
    return hospitals
