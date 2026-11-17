# GPS · 병원 채팅 통합 UI

2026-11-17 / 팀 소스 커밋 `37492e19240490d656e7f4f85cb43f52e986bd0b`

현재는 인증 없는 가상 데이터 검증용입니다. 팀 서버와 QUICK119 서버 모두
`127.0.0.1`에서만 실행하며 LAN·운영 모드 접근을 차단합니다.

## 실행과 사용

프로젝트 루트의 PowerShell 터미널 두 개에서 실행합니다.

```powershell
# 터미널 1: 기존 가상 기록을 유지하고, 가상 자원의 관측 시각만 명시적으로 갱신
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-team-chat.ps1 -RefreshSyntheticResources

# 터미널 2: 키 없이 확인하는 가상 직선 경로 모드
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-chat-api.ps1 -SyntheticRoutes
```

각 터미널의 Ctrl+C로 종료합니다. 이미 실행 중인 서버가 있으면 먼저 해당 실행 터미널에서
종료하거나 서로 다른 `-Port` / `-TeamPort`를 지정합니다. 기존 DB를 삭제하지 않습니다.
스크립트 실행 예외는 해당 PowerShell 프로세스에만 적용하며 영구 실행 정책은 바꾸지 않습니다.

1. 대원: `http://127.0.0.1:18034/transport/`를 엽니다. 홈 하단의 ‘지도 · 채팅’에서도 진입합니다.
2. ‘가상 이송 시작’ → 가상 병원 ‘수용 문의’를 누릅니다.
3. 해당 병원의 ‘의료진 화면 열기’를 새 창에서 엽니다. 서버 주소는 같은 출처로 고정됩니다.
4. 의료진 창에서 가상 담당자 이름을 입력하고 해당 문의의 ‘수용 가능’을 누릅니다.
5. 대원 화면의 ‘이 병원으로 목적지 확정’을 누릅니다. 확정된 병원의 GPS 경로도 조회합니다.
6. ‘실시간 안내 시작’을 누르고, 오른쪽 위 ‘병원 채팅’ / ‘GPS 지도’로 전환합니다.
7. 의료진 창의 ‘도착 예정 · 이송 대화’에서 동일 이송 ID를 선택하고 답장합니다.

가상 이송 ID만 URL에 남습니다. 새로고침 후 같은 ID의 서버 기록은 다시 조회하지만,
초안·미확인 전송 요청·GPS 상태는 새로고침 후 복원하지 않습니다.
일반 QUICK119 환자 등록 기록이나 실제 음성 보고를 가상 이송 건으로 자동 복사하지 않습니다.

## 경로 모드 구분

`-SyntheticRoutes`는 기존 `/api/routes/car`의 **명시적인 로컬 테스트 모드**입니다.
팀의 가상 병원 10곳 좌표만 허용하며 지도에는 직선을, 거리·시간에는 단순 추정값을 표시합니다.
화면에 ‘가상 직선·시간 추정 테스트 / 실제 도로 경로 아님’을 계속 표시합니다.
실제 길안내나 이송 시간 판단에 사용하면 안 됩니다. 지도 타일은 인터넷 상황에 따라 보이지 않을 수 있습니다.

해당 옵션을 빼면 원래 카카오 경로 처리를 그대로 사용합니다. 기본 `start-chat-api.ps1`은
외부 API와 `.env` 로딩을 끄므로 이 경우 경로 조회는 비활성 안내가 나옵니다.
기존 HTTPS/API 실행 스크립트, `api_integrations.py`, 키 설정은 변경하지 않았습니다.
이번 검증에서는 실제 API 키나 `.env`를 읽거나 실경로 API를 호출하지 않았습니다.

## 동작과 수명

- 두 패널은 한 페이지에서 한 번 생성하고 표시만 바꿉니다. 전환으로 GPS watcher,
  채팅 객체, 커서, 지도 인스턴스를 다시 만들거나 페이지를 이동하지 않습니다.
- 채팅 화면에서도 GPS 정확도·위치 튐·경로 이탈 검사와 기존 호출 제한이 계속 실행됩니다.
- 지도 숨김 중에는 위치·경로 데이터를 갱신하고 지도 렌더링을 보류합니다. 복귀 시
  `invalidateSize({pan:false, animate:false})`와 최신 경로를 적용합니다.
  확대와 따라가기 ON/OFF를 유지하고, ON이면 마지막 유효 GPS, OFF이면 수동 중심을 복원합니다.
- 기존 팀 API로 수용 문의 → 의료진 수용 → 대원 목적지 확정을 진행합니다.
  목적지 확정 전에는 대기 상태이며 입력은 잠깁니다. 팀 API가 허용하는 미확정 개인 기록도
  이번 사용자 UI에서는 전송하지 않습니다.
- 페이지가 전면에 있으면 어느 패널에서도 중첩 없이 3초 폴링합니다. `has_more` 페이지를
  먼저 소진하고 사건·메시지 커서를 따로 갱신합니다. ID로 중복 제거, `order_key`로 정렬합니다.
- 지도에 있는 동안 새 의료진 메시지를 받으면 배지를 표시합니다. 최초 과거 기록은 세지 않습니다.
  채팅에서 마지막 메시지를 보거나 ‘새 메시지 확인’을 눌렀을 때 페이지 안의 미확인 표시를 지웁니다.
  이는 서버 읽음 확인 기능이 아닙니다.
- 미확인 전송은 원래 ID·본문·참여 구간·발신 이름 그대로 재시도합니다.
  POST 성공만으로 GET 커서를 앞당기지 않으며 저장 영수증을 대조한 뒤 요청을 지웁니다.
  병원 참여 구간이 바뀐 초안은 사용자 확인 전까지 전송을 막습니다.
- ‘안내 종료’는 GPS만 종료합니다. ‘채팅 연결 종료’는 로컬 채팅 폴링만 중단하며
  서버 대화방·이송을 취소하거나 GPS를 끄지 않습니다. 재연결 시 놓친 메시지를 조회합니다.
- 앱 최소화·화면 잠금·페이지 이탈은 기존 GPS 안전 중단을 유지합니다.
  채팅 조회도 일시정지하며 복귀 시 커서로 재조회합니다. GPS는 사람이 다시 시작해야 합니다.

## 의료진 화면 재사용과 정보 보관

`/hospital/`은 고정 커밋의 `hospital-ui` HTML·모듈·CSS를 재사용합니다.
원본 폴더를 수정하지 않고 응답 시 최소 어댑터를 적용합니다.

- 원본의 모든 `localStorage` 참조를 페이지별 Storage 호환 메모리 객체로 교체합니다.
  원본 전송 복구 모듈의 저장 후 읽기 확인도 동일 메모리 객체로 동작합니다.
- 서버 주소 설정을 같은 출처로 제한하고 CSP `connect-src 'self'`를 적용합니다.
- 이번에 연결한 수용 문의·이송 대화만 표시합니다. 자원·제한·계측·도착 처리의 미연결 조작은 숨깁니다.
- 양쪽 화면의 메시지·초안·환자정보를 localStorage/sessionStorage/IndexedDB에 추가하지 않습니다.
  동적 HTTP 응답은 `no-store`이며 기존 SW 정적 허용 목록은 변경하지 않았습니다.
- 팀 DB와 QUICK119 DB는 계속 별도입니다. 인증·권한이 구현되기 전에는 실제 환자를 넣지 않습니다.

## 변경 파일

| 구분 | 파일 |
| --- | --- |
| 페이지·병원 어댑터 | `main.py`, `team_chat_ui.py`, `team_chat_gateway.py` |
| 화면 | `templates/transport.html`, `transport_workspace.html`, `car_route_panel.html`, `base.html` |
| 화면 전환·채팅 | `static/transport-workspace.js`, `hospital-chat.js`, `transport-workspace.css` |
| 기존 GPS 확장 | `static/car-routes.js` |
| 의료진 메모리·테마 | `static/hospital-memory.js`, `hospital-adapter.css` |
| 선택 가상 경로 | `synthetic_routes.py` |
| 실행 | `scripts/start-chat-api.ps1`, `start-team-chat.ps1`, `run-team-chat.py` |
| 검증 | `tests/test_team_chat.py`, `transport_harness.js`, `chrome_driver.py` |

원래 GPS 필터 상수·호출 제한·응급실 조회·환자 DB 스키마·음성 처리·PWA manifest/SW 정책을 보존했습니다.
이번 작업 전 소스 백업은 `.local/backups/ui-20261117-172902/`에 있습니다.
`.env`, 인증서, 기존 DB는 백업·복사·업로드하지 않았습니다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/verify-team-source.py
.\.venv\Scripts\python.exe -m pip check
```

- 전체 52개 통과, skip 없음: 기존 회귀 40개 + 연결·화면·보안 검증 12개.
- 기존 실시간 GPS 합성 검사 62항목 통과.
- 새 GPS+채팅 동시 실행 합성 브라우저 검사 73항목 통과.
- 실제 별도 팀 서버·FastAPI·두 Chrome 창에서 수용 → 확정 → 양방향 대화·배지 검증.
  실제 GPS와 경로만 가상으로 주입했으며 채팅은 실제 HTTP를 사용했습니다.
- 대원 360/375/390/412/430px, 의료진 768/1024px에서 가로 넘침 없음.
- 양쪽 브라우저의 영구 저장소 쓰기를 차단한 상태에서도 메시지 전송 성공.
- 고정 팀 소스 1,947개 파일의 바이트 일치 확인, 기존 환경 `pip check` 통과.
- Chrome 준비 파일의 짧은 Windows 파일 잠금을 대기하도록 기존 테스트 도우미를 보완했습니다.

실서버 화면 캡처는 `.local/test-runs/04b4674b2f574dcbb8a568ed83f0aedf/`의
`mobile-map.png`, `mobile-chat.png`, `hospital-chat.png`에 있습니다.

## 남은 제한

실제 갤럭시 설치 PWA·GPS 센서·화면 잠금·가상 키보드는 실기기 미검증입니다.
모바일 크기는 PC Chrome 에뮬레이션 결과입니다. 테스트 모드의 직선은 실제 도로 경로가 아닙니다.
사용자 인증·병원 소속·담당 이송 권한이 없어 외부 공개와 LAN 접속을 허용하지 않습니다.
백그라운드 수신·푸시·서버 읽음 표시는 제공하지 않습니다.
진짜 환자 프로필·음성 보고와 이송 건 연결 및 실제 의료기관 ID 매핑은 후속 작업입니다.
팀의 자동 문의·배정 워커는 계속 비활성입니다.
