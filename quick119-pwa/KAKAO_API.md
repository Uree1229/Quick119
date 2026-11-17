# 카카오모빌리티 1단계 검증

기존 `KAKAO_REST_API_KEY` 하나를 그대로 사용합니다. `.env`는 기존 FastAPI lifespan에서 명시적으로 허용한 개발/운영 모드에만 읽고, 프로세스 환경변수를 덮어쓰지 않습니다. 키는 요청 시점에 읽으며 클라이언트나 로그로 보내지 않습니다. 시작 로그에는 외부 API 활성화 여부와 키 로딩 여부만 기록합니다.

## 서버 API

`POST /api/routes/car`

```json
{
  "origin": {"lat": 37.402464820205246, "lon": 127.10764191124568},
  "destination": {"lat": 37.39419693653072, "lon": 127.11056336672839}
}
```

카카오 `GET https://apis-navi.kakaomobility.com/v1/directions`를 서버에서 호출합니다. 출발지·목적지는 경도,위도 순서로 전송합니다. 성공 응답은 `distance_m`, `duration_sec`, `distance_km`, 올림한 `duration_min`, `[경도, 위도]` 쌍의 `coordinates`를 제공합니다. 일반 자동차의 최단 시간 경로입니다.

`POST /api/routes/car/compare`

```json
{
  "origin": {"lat": 37.402464820205246, "lon": 127.10764191124568},
  "destinations": [
    {"key": "synthetic-er-1", "lat": 37.39419693653072, "lon": 127.11056336672839}
  ]
}
```

카카오 `POST https://apis-navi.kakaomobility.com/v1/destinations/directions`를 한 번 호출합니다. 목적지는 1~5개이고 `key`는 서로 달라야 합니다. `x=경도`, `y=위도`로 전송하고, 응답 순서와 관계없이 `key`로 결과를 연결합니다. 실패하거나 누락된 목적지도 개별 상태로 반환합니다.

다중 목적지는 요약 정보만 제공하며 공식 최대 탐색 반경은 **10km**입니다. 반경 밖 응급실의 비교 및 상세 경로 UI 처리는 2단계에서 결정해야 합니다. 단일 목적지 상세 경로 API는 별도로 준비했습니다.

응답은 `Cache-Control: no-store`이며 DB나 PWA에 저장하지 않습니다. 서버 메모리 캐시는 최대 128건, 성공 30초·실패 5초입니다. 외부 응답 본문과 예외 문자열을 노출하지 않으며 인증 실패, 권한 부족, 쿼터 초과, 시간 초과, 잘못된 응답을 구분합니다. 좌표 범위·유한수·중복 목적지를 검증합니다. HTTP 200이어도 개별 `code`를 확인해야 합니다.

## 재검증

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-https-api.ps1 -CheckKakaoApi
./.venv/Scripts/python.exe -m unittest discover -s tests -p test_app.py
```

`-CheckKakaoApi`는 서버를 계속 실행하지 않고 종료합니다. 정상 HTTPS 실행과 동일한 환경 플래그 및 lifespan 로더를 사용하며, 공개 샘플 좌표·임시 DB로 두 API를 점검합니다. 일반 서버 실행은 기존처럼 `-CheckKakaoApi` 없이 실행합니다. FastAPI TestClient를 통한 검사이므로 HTTPS 전송이나 실제 갤럭시 GPS를 검증하는 검사는 아닙니다.

2026-11-17 검증 결과:

| 항목 | 결과 |
| --- | --- |
| `.env` 키 로딩 | `key_loaded=true`, 키 미출력 |
| 단일 목적지 | 서버/카카오 HTTP 200, `result_code=0`, 1,612m, 380초, 경로 좌표 27개 |
| 다중 목적지 5곳 | 서버/카카오 HTTP 200, 5곳 모두 `result_code=0` |
| 자동화 검사 | 37개 전체 통과, 신규 카카오 검사 6개 포함 |

거리와 시간은 교통 상황에 따라 달라집니다. 최초 샌드박스 실행은 네트워크 및 Chrome GPU·권한 제한으로 실패했고, 권한이 허용된 실행에서 실제 API 호출과 전체 회귀 검사를 통과했습니다. 실제 환자 DB는 사용하지 않았습니다.

## 2단계 화면 연결

응급실 목록에 다중 목적지 API의 주행 거리·예상시간을 표시합니다. 10km 밖 응급실은 비교 제한을 안내하며, 해당 병원의 자동차 경로 버튼으로 개별 조회할 수 있습니다. 길찾기 실패가 병원 목록 조회를 막지 않습니다.

병원 상세정보의 **현재 GPS에서 자동차 경로 보기**를 누르면 위치 권한을 요청하고 최신 GPS에서 선택한 응급실까지 도로 경로·거리·예상시간을 표시합니다. **현재 GPS로 다시 계산**은 위치를 새로 확인합니다. 권한 거부·시간 초과·인증 및 네트워크 오류 시 이전 경로를 숨기고 재시도할 수 있습니다.

경로 계산은 서버의 카카오모빌리티 REST API, 지도 표시는 로컬에 포함한 Leaflet 1.9.4와 OpenStreetMap 타일을 사용합니다. 추가 지도 JavaScript 키는 필요 없습니다. 타일 서버에는 사이트 origin만 Referer로 전송하며 환자·GPS가 담긴 페이지 URL을 보내지 않습니다. 타일을 PWA에서 사전 다운로드하지 않습니다. 배경지도 실패 시에도 거리·시간은 확인할 수 있고 카카오맵 링크로 이동할 수 있습니다.

화면은 경로를 확인하는 기능이며 이동 중 자동 GPS 추적이나 회전별 음성 안내를 제공하지 않습니다. 별도 내비게이션은 **카카오맵에서 길찾기 열기**로 이어집니다.

사용하려면 기존 HTTPS API 서버를 재시작한 후 응급실 화면에서 위치를 확인하고 병원 상세정보를 펼치세요. 실제 갤럭시 GPS·권한·지도 타일 연결은 기기에서 확인해야 합니다.

2단계 검증 결과: 전체 39개 테스트 통과. 가상 병원·모의 GPS로 실제 브라우저의 경로 표시, 지도 선·마커, 최신 GPS 재계산, 권한 거부 후 재시도, 360/412/1024px 화면을 확인했습니다. 테스트 중 외부 지도 타일은 차단하여 배경지도 실패 상태도 확인했습니다. 공개 샘플 좌표로 재실행한 카카오 실제 호출은 단일/다중 모두 HTTP 200이며 5곳 모두 성공했습니다.

공식 문서: [자동차 길찾기](https://developers.kakaomobility.com/guide/navi-api/directions.html), [다중 목적지 길찾기](https://developers.kakaomobility.com/guide/navi-api/destinations.html).
