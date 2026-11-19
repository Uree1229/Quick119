# 팀 채팅 서버 준비 결과

> 이 문서는 11월 17일 서버 연결 실험 기록입니다. 현재 시연은 같은 저장소의 `server/`, `hospital-ui/`, `quick119-pwa/`를 사용합니다. 현재 실행 방법은 [TRANSPORT_UI.md](TRANSPORT_UI.md)를 따르세요.

기준일: 2026-11-17 (Asia/Seoul)

이번 단계는 팀 코드 확보, 가상 서버 실행, 기존 FastAPI와의 HTTP 연결까지입니다.
GPS + 채팅 전환 UI는 구현하지 않았습니다. 실제 사용자 인증은 아직 없습니다.

## 보존과 분리

- 백업: `.local/backups/20261117-171416/source/` — 기존 코드·정적 파일·문서·테스트 50개.
  `manifest.json`의 SHA-256으로 원본과 복사본을 대조했습니다.
- 백업 제외: `.env`, API 키·인증서, 모든 DB, 가상환경, 캐시, IDE 설정.
  이 백업은 개인정보를 제외한 소스 백업이며 운영 데이터 복구용 백업이 아닙니다.
- 팀 소스: `.local/team-source/Quick119-37492e19240490d656e7f4f85cb43f52e986bd0b/`
- 원본: https://github.com/Uree1229/Quick119/tree/37492e19240490d656e7f4f85cb43f52e986bd0b
- 다운로드: `.local/downloads/Quick119-37492e19240490d656e7f4f85cb43f52e986bd0b.zip`
- ZIP SHA-256: `d817a5c7926e6ebafb42bb2a86d4c940139608282311fccbfd93ba025de8de9b`
- 별도 Python: `.local/team-venv/` (Python 3.13.2, 표준 라이브러리만 필요).
- 팀 DB: `.local/team-chat/chat-synthetic.db`
- 연결 검증용 QUICK119 DB: `.local/team-chat/quick119-synthetic.db`
- 기존 `emergency.db`, `data/quick119-dev.db`는 열거나 복사하지 않았습니다.
- `.local/`은 Git 제외 대상입니다. 기존 작업 폴더에는 `.git`이 없고 Git 실행 파일도
  발견되지 않아 미커밋 상태 비교는 할 수 없었습니다. 원본 파일 보존은 백업 해시로 확인했습니다.

## 실행

프로젝트 루트에서 PowerShell 터미널 두 개를 사용합니다.

터미널 1:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-team-chat.ps1
```

터미널 2:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-chat-api.ps1
```

기본 주소:

- 팀 공통 서버: `http://127.0.0.1:18033/api/team-runtime`
- QUICK119를 통한 동일 API: `http://127.0.0.1:18034/api/team-runtime`
- QUICK119 기존 화면: `http://127.0.0.1:18034/`

종료는 각 터미널에서 Ctrl+C입니다. 실행 스크립트는 자기 자식 프로세스를 정리합니다.
포트 충돌 시 다른 포트를 지정합니다. 기존 서버를 강제로 종료하지 않습니다.
현재 Windows의 스크립트 실행 제한 때문에 위 명령은 해당 PowerShell 프로세스에만
실행 정책 예외를 적용합니다. 사용자·시스템의 영구 실행 정책은 변경하지 않습니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-team-chat.ps1 -Port 18133
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-chat-api.ps1 -Port 18134 -TeamPort 18133
```

실행 스크립트는 환경을 제한해서 자식 프로세스에 전달합니다. `.env`를 읽지 않고
외부 API·접근 로그를 끕니다. 기존 HTTPS/API 실행 스크립트와 기본 실행 설정은 변경하지 않았습니다.
이 로컬 HTTP 실행은 백엔드 검증용이며 갤럭시 HTTPS·설치 PWA 실기기 검증이 아닙니다.

## 팀 코드 재사용 범위

팀의 `server.team_runtime.CommonRuntime`을 원본 수정 없이 사용합니다.
`voice-only`는 팀이 정한 실행 모드 이름입니다. 수동 문의, 의료진 응답, 목적지 확정,
대화 조회·전송은 사용할 수 있지만 배정 솔버와 자동 문의 워커는 실행하지 않습니다.
이번 채팅 검증에는 OR-Tools 등의 full 모드 패키지를 설치할 필요가 없습니다.
팀의 원래 Python/SQLite/솔버 벤치마크 환경과 일치한다고 주장하지 않습니다.
원본 팀 소스의 프로필 검사는 통과했습니다.

DB는 팀의 전용 표식과 가상 병원 10곳을 검증합니다. 다른 기존 DB를 초기화하거나
`--seed`로 덮어쓰는 명령은 사용하지 않습니다. 저장된 가상 병원 자원이 오래되어
수용·확정이 차단되면 팀의 명시적 가상 자원 갱신 기능을 검토하세요. 서버는 시작할 때
자원 시각을 자동 갱신하거나 기존 DB를 삭제하지 않습니다.

## FastAPI 연결

`main.py`에는 `team_chat_gateway.py` 라우터 등록만 추가했습니다.
기존 `database.py`, `settings.py`, `api_integrations.py`, GPS·음성·PWA 자원은 유지합니다.

연결 모듈은 기본 비활성입니다. 실행 스크립트가 아래 설정을 해당 자식 프로세스에만 적용합니다.

```text
QUICK119_TEAM_CHAT_ENABLED=1
QUICK119_TEAM_CHAT_URL=http://127.0.0.1:18033
QUICK119_ENV=development
QUICK119_LOAD_ENV=0
QUICK119_EXTERNAL_API=0
QUICK119_DB_PATH=<프로젝트>/.local/team-chat/quick119-synthetic.db
```

- 운영 모드, LAN 클라이언트, 다른 출처 요청, 외부 서버 주소, 사용자정보 포함 URL을 차단합니다.
- 매 요청에서 기존 `/api/team-runtime`으로 가상 실행 표식·DB 유효성을 확인합니다.
- 요청 본문·응답 크기와 연결 시간을 제한하고 리다이렉트·환경 프록시를 사용하지 않습니다.
- 클라이언트의 Cookie·Authorization·임의 헤더를 팀 서버로 전달하지 않습니다.
- 응답은 `Cache-Control: no-store`이며 인증키·업스트림 예외 원문을 오류로 노출하지 않습니다.
- 생성할 이송 ID는 `Q119-TEST-` 접두어와 `extra.synthetic_fixture: true`가 필요합니다.
  병원 ID는 가상 병원 H01~H10입니다. 이 표시가 입력 내용을 익명화하는 것은 아니므로
  실제 이름·연락처·환자정보를 넣지 마세요.

노출한 경로는 모두 팀의 기존 API입니다.

```text
GET  /api/team-runtime
GET  /api/hospitals
GET  /api/hospitals/{hid}
GET  /api/hospitals/{hid}/inquiries
GET  /api/hospitals/{hid}/incoming
POST /api/patients
GET  /api/patients/{pid}
POST /api/patients/{pid}/inquiries
POST /api/inquiries/{iid}/respond
POST /api/patients/{pid}/confirm
GET  /api/patients/{pid}/conversation
POST /api/patients/{pid}/messages
GET  /api/hospitals/{hid}/patients/{pid}/conversation
POST /api/hospitals/{hid}/patients/{pid}/messages
```

의료진 화면 원본은 확보했지만 이번 단계에서 QUICK119에 연결하거나 보관 정책을
변경하지 않았습니다. 팀 `/hospital/` 원본의 미확인 전송 원문 localStorage 보관은
후속 UI 통합 시 조정해야 합니다. 이번 검증은 화면을 사용하지 않는 HTTP 검증입니다.

## 검증 재실행

```powershell
.\.venv\Scripts\python.exe scripts/verify-team-source.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_team_chat.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_app.py -v
```

팀 원본 테스트는 팀 소스 폴더를 작업 디렉터리로 사용합니다.

```powershell
Push-Location .local/team-source/Quick119-37492e19240490d656e7f4f85cb43f52e986bd0b
try {
  ..\..\team-venv\Scripts\python.exe scripts/test_suites.py python test_conversations test_server -v
} finally { Pop-Location }
```

기록한 결과:

- 팀 원본 채팅 23개 + 서버 21개 = 44개 통과, skip 없음.
- ZIP의 소스 파일 1,947개와 추출된 파일의 바이트가 모두 일치했습니다.
- 신규 연결 검증 8개 통과: 실제 별도 팀 Python 프로세스와 FastAPI HTTP 서버 사용.
- 기존 회귀 40개 모두 최종 통과. 최초 샌드박스 실행의 Chrome GPU 제한 후 권한을 조정해
  재실행했고, 38개 통과·2개 DevTools 파일 잠금 실패가 발생해 그 2개만 재실행하여 통과했습니다.
- GPS 실시간 안내의 합성 브라우저 검사 62항목 통과.
- 기존 환경 `pip check`: 깨진 의존성 없음. 기존 Starlette/httpx 사용 중단 예정 경고는 유지됩니다.

실제 HTTP 검증에는 수용 전 병원 접근 차단, 의료진 수용 후에도 확정 전 접근 차단,
확정 후 양방향 메시지, 중복 전송, 잘못된 참여 구간, 무관한 병원 차단,
서로 다른 이송 기록 분리, 커서 페이지 처리와 로컬/팀 DB 스키마 분리를 포함했습니다.
신규 테스트는 `.local/test-runs/<무작위 ID>/`에 새 가상 DB와 로그만 생성하고
종료 시 자신이 실행한 프로세스를 정리합니다.

다음 단계는 승인된 화면 설계에 따른 GPS·채팅 독립 실행과 내부 패널 전환입니다.
실제 사용자 인증·병원 및 이송 접근 권한은 그와 별도로 필요합니다.
