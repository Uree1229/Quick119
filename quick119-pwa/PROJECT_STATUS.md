## 2026-11-16 현장 환자 상태 보고 개선

- 응급상황 접수 시작 → 현장 환자 상태 음성 보고 → 같은 화면에서 수정·최종 저장으로 연결합니다. 새 흐름은 항목별 질문 화면으로 이동하지 않습니다. 기존 단계별 경로는 기존 기능 호환을 위해 유지합니다.
- `voice_intake.html`은 `base.html`과 기존 QUICK119 색상 변수·버튼을 재사용합니다. 환자·보호자 대상 문진을 현장 대원의 일괄 보고 안내로 교체했습니다.
- `static/voice-intake.js`: 기존 한국어 SpeechRecognition / webkitSpeechRecognition, continuous / interimResults를 재사용합니다. 원문 말풍선·실시간 요약·직접 입력·수동 수정 보호·명시적 재추출·새 환자 초기화를 제공합니다. 확정되지 않은 중간 인식은 저장하지 않습니다.
- 추출은 외부 AI/API 없는 규칙 방식입니다. 나이·성별·의식·호흡·증상·발생 시간·수축기/이완기 혈압·맥박/심박수·체온·산소포화도를 지원합니다. 단위·소수점·부정 표현을 처리하고 서로 다른 수치가 함께 있으면 자동 선택하지 않습니다. 지원하지 않는 표현은 수동 확인해야 하며 임의의 정상값이나 진단을 생성하지 않습니다.
- 화면의 빈 일반 항목은 미확인, 빈 활력징후는 미측정입니다. 서버에는 빈 항목을 null로 저장합니다. 수동 수정한 구조화 값과 보고 원문을 분리하여 보존합니다. 원문 직접 입력/수정도 가능하며 요약 수정은 원문을 변경하지 않습니다.
- `POST /api/emergency-intakes`: request_id(UUID), 선택적 patient_id, transcript, state를 검증합니다. 별도 `emergency_intakes` 테이블에 JSON 구조화 상태·원문·UTC 저장 시각을 기록합니다. 기존 patients 스키마·행을 변경하지 않습니다. 새 테이블은 실제 앱의 다음 시작 시 선택된 DB에 추가되며 이번 검증에서는 임시 테스트 DB만 사용했습니다.
- 동일 요청 재시도는 같은 접수번호를 반환합니다. 같은 요청번호의 다른 내용은 409, 없는 환자는 404, 저장 장애는 503, 잘못된 입력은 422입니다. 클라이언트는 저장 중 조작을 잠그고 실패 시 내용을 유지합니다. 성공 후에는 새 환자 보고로 시작합니다.
- 이전 버전 emergencyVoiceIntake 임시 결과는 새 화면 진입/초기화 시 제거하여 다른 환자와 혼합되지 않게 했습니다. 새 보고는 sessionStorage/Cache Storage에 저장하지 않습니다. HTTP no-store와 SW의 POST 제외 정책을 유지합니다.
- SW 캐시 버전 v2, 정적 허용 목록에 새 JS만 추가했습니다. 실제 119 신고·병원 자동 전송·외부 API 기능은 추가하지 않았습니다. GPS·병원 조회·환자 등록과 관리·PWA의 기존 동작을 유지합니다.

검증 결과: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` → 27개 모두 통과, skip 없음. 별도 임시 DB, .env 읽기 차단, 외부 병원 API 차단 환경으로 실행했습니다. 브라우저는 임시 Chrome 프로필과 loopback 서버를 사용했습니다. 초기 샌드박스 Chrome GPU 실행 제한으로 브라우저 테스트가 실패하여, 실행 권한을 조정한 동일 테스트에서 모두 통과했습니다. 기존 Starlette/httpx deprecation 경고는 남아 있습니다.

서버 저장/실패/검증/중복 방지, 모의 음성 중간·확정 결과, 수동 수정 보호, 부정 표현, XSS 문자 출력, SW 캐시 제외, 360/375/390/412/430px × 4개 화면의 20개 레이아웃을 확인했습니다. 기존 patients 스키마 보존과 환자 관리·접수·GPS 관련 렌더링·병원 조회·PWA 회귀 테스트도 통과했습니다.

실제 마이크의 음성인식 정확도, 실제 HTTPS 인증서 및 Android 설치 PWA는 실기기 미검증입니다. 지원되지 않는 한국어 수사·복잡한 문맥은 미확인 처리될 수 있어 대원의 확인이 필요합니다. 실제 .env·API 키·인증서·기존 DB는 읽거나 수정하지 않았습니다.

---

# QUICK119 프로젝트 상태 / 개발 인수인계

기록일: 2026-11-15 (Asia/Seoul)

## 최신 구현: Android 설치형 PWA 기반 추가

- FastAPI에 `/manifest.webmanifest`, 루트 `/sw.js`, `/pwa-check`를 추가했습니다. 기존 URL·DB 스키마와 음성 문진 우선 흐름은 유지했습니다.
- 앱 이름 `QUICK119`, 표시 이름 `퀵119`, `display: standalone`, `scope: /`, 시작 URL `/?source=pwa`를 적용했습니다. 실제 설치 아이콘에서 실행할 때 주소창 없는 앱 화면을 요청하는 설정입니다. Android 실기기에서 해당 표시를 확인한 상태는 아닙니다.
- 네이비 배경·빨간 십자가 로고의 PNG 192/512 및 512 maskable 아이콘을 생성했습니다. 원본 SVG와 표준 라이브러리 생성 스크립트를 포함하며 외부 이미지 생성·인증키를 사용하지 않았습니다.
- Android 기본 스플래시용 이름·아이콘·배경색을 구성했습니다. 시작 화면 모양·지속 시간은 Android/Chrome이 결정하며 별도의 강제 대기 없이 기존 홈을 시작 화면으로 사용합니다.
- 공통 Manifest/설치 스크립트를 새 화면과 기존 HTML 화면 모두에 연결했습니다. 설치 이벤트가 있을 때 버튼을 표시하고 없으면 Chrome 메뉴 설치 안내를 제공합니다.
- SW는 허용 목록에 있는 개인정보 없는 정적 자원 9개만 저장합니다. 동적 HTML·환자·문진·위치·병원/API·POST는 저장하지 않습니다. 동적 HTTP 응답에도 no-store, no-referrer와 같은 출처의 마이크/GPS 권한 정책을 추가했습니다.
- 오프라인에서는 안전한 안내와 119 전화 링크를 제공합니다. 업데이트가 문진을 강제 새로고침하지 않게 했습니다.
- `/pwa-check`에 설치 상태·보안 컨텍스트·음성 API 지원·마이크·GPS 점검을 추가했습니다. 점검의 마이크는 즉시 종료하고 GPS는 정확도만 표시하며 서버로 좌표를 보내거나 저장하지 않습니다.
- 기존 병원 화면의 GPS는 사용자 버튼 조작 후 요청하며 HTTPS 미사용 시 안내합니다. 음성인식도 보안 컨텍스트를 확인하고 직접 입력을 유지하며 저장소 차단 오류를 처리합니다.
- `scripts/start-https.ps1`과 [ANDROID_PWA.md](ANDROID_PWA.md)에 사설망 HTTPS·Android CA 신뢰·설치·권한 검증 방법을 추가했습니다. 인증서·실제 `.env`·운영 DB·외부 API는 사용하지 않았습니다. 인증서 파일은 Git 제외 대상입니다.

이번 변경 파일:

- 수정: `main.py`, `templates/base.html`, `templates/voice_intake.html`, `static/quick119.css`, `.gitignore`, `tests/test_app.py`, 이 문서.
- 생성: `static/manifest.webmanifest`, `static/sw.js`, `static/pwa.js`, `static/pwa.css`, `static/offline.html`, `static/app-icons/icon.svg`, `icon-192.png`, `icon-512.png`, `maskable-512.png`, `templates/pwa_head.html`, `templates/pwa_controls.html`, `templates/pwa_check.html`, `scripts/generate_icons.py`, `scripts/start-https.ps1`, `tests/sw_harness.js`, `tests/pwa_live_harness.js`, `tests/chrome_driver.py`, `ANDROID_PWA.md`.

검증 결과는 아래 최신 테스트 기록을 기준으로 합니다. 이전 절의 미구현·미검증 내용은 초기 개발 시점 기록입니다. Android 실기기·실제 인증서 HTTPS·음성/GPS 점검은 아직 미실행입니다. 현재 인증 없는 환자 경로 때문에 공개 서버 배포는 하지 않았습니다.

### 최신 PWA 테스트 기록

- 최종 명령: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` → **23개 모두 통과**, skip 없음.
- `pip check` → `No broken requirements found.`
- PC 환경: Windows / Python 3.13 / Chrome 158.0.8127.36, 별도 임시 프로필. Android 실기기가 아닙니다.
- 기존 회귀 16개 외에 Manifest·PNG 치수/응답, 새/기존 페이지의 PWA 연결, 민감 응답 no-store, SW MIME/범위/허용 목록, 권한 점검·직접 입력 대체, SW 모의 이벤트, 실제 PC Chrome 등록·화면 검사를 추가했습니다.
- Chrome JS 엔진에서 SW 모의 이벤트 10개 검증: 정적 사전 캐시, 자격정보 제외, 동적/API·POST·외부/쿼리 요청 제외, 정적 캐시 적중, 내비게이션 무저장, 오프라인 안내/503, 런타임 무저장, 해당 앱의 이전 캐시만 정리.
- 실제 loopback Uvicorn 테스트 서버와 PC Chrome에서 SW 등록·활성화·정적 파일 9개 사전 저장을 확인했습니다. 환자·요약·좌표를 포함한 테스트 요청 후 캐시 키 목록이 늘지 않았습니다.
- PC Chrome iframe의 360/375/390/412/430 CSS px × 홈·접수 선택·점검 화면 3개 = 15개 조합에서 가로 넘침이 없었습니다. 실제 Android 키보드·터치·가로 방향·전체 기존 화면 검증을 대신하지 않습니다.
- HTTPS 시작 스크립트는 PowerShell 구문 검사를 통과했습니다. mkcert·서버 인증서·Android CA 신뢰는 준비하지 않았으므로 실제 TLS 연결 검증은 하지 않았습니다.
- 초기에 샌드박스의 Chrome GPU 프로세스 접근 실패가 있어 승인된 환경에서 실행했습니다. 실제 SW 준비가 끝나기 전에 종료되던 가상 시간 기반 테스트는 CDP로 완료 신호를 기다리도록 수정해 통과했습니다.
- 기존 Starlette/httpx TestClient의 deprecation 경고는 남아 있으며 테스트 실패는 아닙니다.
- 테스트 서버는 loopback에만 바인딩했고 종료했습니다. 실제 `.env` 읽기·운영 DB 연결·외부 병원 API 호출은 차단했습니다. 실제 사용자 프로필·인증서 설치·배포는 수행하지 않았습니다.

## 최종 목표와 필수 개발 기준: Android 설치형 PWA

사용자 확정 요구사항입니다. 이후 개발·인수인계에서도 아래 기준을 유지합니다.
이 기준을 문서화한 후 최신 작업에서 Manifest·PNG 아이콘·Service Worker와 점검 화면을 구현했습니다. Android 실기기 완료 기준은 여전히 별도로 검증해야 합니다.

| 항목 | 구현 및 완료 기준 | 현재 검증 상태 |
| --- | --- | --- |
| 주요 환경 | 실제 Android 스마트폰의 Chrome 및 홈 화면에서 실행한 설치 앱. 기기명·Android 버전·Chrome 버전 기록 | 실기기 미검증 |
| 화면 너비 | 360 / 375 / 390 / 412 / 430 CSS px를 점검하고 가로 화면·확대·키보드 표시 시에도 주요 버튼과 입력값이 가려지지 않게 구현 | PC Chrome에서 새 3개 화면의 15개 조합 가로 넘침 없음 / Android·키보드·전체 화면 미검증 |
| 터치 조작 | 주요 버튼·링크·입력창은 최소 48 CSS px 높이, 충분한 간격, 입력 글자 16px 이상을 기본으로 적용. 숫자·전화 입력 키보드와 명확한 포커스·선택 상태 제공 | 현재 화면 전체 준수 여부 미검증 |
| Manifest | `name`, `short_name`, 안정적인 `id`, `start_url`, `scope`, `display: standalone`, 브랜드 색상, 아이콘 정의 및 공통 HTML 링크 추가 | 구현 / HTTP·내용 검증 |
| 아이콘 | 실제 PNG 192×192 / 512×512 제공. maskable 아이콘은 안전영역을 지켜 별도 준비·확인 | 구현 / PNG 치수·전용 아이콘 시각 확인 / Android 미검증 |
| Service Worker | 루트 `/sw.js`에서 제공하고 범위 `/`로 등록. 정적 자원만 허용 목록으로 캐시하고 업데이트·오프라인 안내 구현 | 구현 / PC Chrome 등록·캐시 검증 / Android 미검증 |
| 마이크·GPS | Android Chrome에서 신뢰되는 HTTPS 사용. 권한 요청은 설명과 사용자 조작 후 수행하고 거부·취소·시간 초과·재시도 처리 | 실기기 미검증 |
| 음성 대체 입력 | 기존 직접 입력 유지. 음성 API 미지원, 권한 거부, 음성 서비스 장애·네트워크 오류에도 텍스트로 진행 가능 | 코드 있음 / 실기기 미검증 |
| 민감정보 캐시 금지 | 환자 정보·문진·위치·응급실/API 응답을 Service Worker 또는 Cache Storage에 저장하지 않음 | 구현 / 모의 이벤트 및 PC Chrome 캐시 불변성 검증 / Android 미검증 |
| 배포 승인 기준 | 환자 접근 권한과 HTTPS 보안 점검 후 배포. 설치·권한·캐시 실기기 결과를 기록한 경우에만 해당 항목 검증 완료 표시 | 점검·배포 전 |

마이크 장치 접근·Geolocation·Service Worker의 보안 컨텍스트 제약과 설치 구성을 공식 문서에서 확인했습니다. HTTPS만으로 음성인식 지원이나 권한 승인이 보장되지는 않으므로 기능 감지와 직접 입력을 유지합니다.

- 설치 구성: https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable
- 보안 컨텍스트: https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts/features_restricted_to_secure_contexts
- 마이크 장치 접근: https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia

### Service Worker 및 캐시 구현 정책

- 같은 출처의 명시된 CSS·JS·아이콘·개인정보 없는 전용 오프라인 HTML만 캐시합니다. `/static/` 경로 전체를 무조건 캐시하지 않고 파일 단위 허용 목록을 사용합니다.
- 환자/접수 관련 HTML을 포함해 일반 페이지 응답, 쿼리 문자열에 위치·환자·증상이 포함된 요청, POST, 외부 API 응답은 캐시에 넣지 않습니다. 모든 GET 요청을 저장하는 전략은 금지합니다.
- `/patients`, `/register`, `/emergency` 및 하위 경로와 향후 환자·위치·병원 API는 네트워크 응답만 사용합니다. 민감 페이지에는 서버·프록시의 `Cache-Control: no-store` 정책도 별도로 적용합니다.
- 캐시 실패나 오프라인 상태에서 과거 환자 정보·병상 수치를 대신 반환하지 않습니다. 개인정보 없는 오프라인 안내를 보여 주고 재시도를 제공합니다.
- 업데이트 때 QUICK119가 소유한 이전 버전 캐시만 정리합니다. 실기기 DevTools의 Application/Cache Storage에서 민감정보 미저장을 확인합니다.
- 음성 결과의 기존 sessionStorage는 SW 캐시와 별개입니다. 접수 종료·환자 전환·로그아웃 시 수명과 삭제 정책을 별도로 개선합니다.

### 같은 Wi-Fi에서 화면을 확인하는 방법: HTTP 개발용

PC와 스마트폰을 같은 신뢰할 수 있는 Wi-Fi에 연결하고 프로젝트 루트 PowerShell에서 실행합니다. 아래는 안내 절차이며 이번 작업에서 서버 실행·방화벽 변경·스마트폰 접속을 수행하지 않았습니다.

```powershell
$env:QUICK119_ENV = "development"
$env:QUICK119_DB_PATH = "data/quick119-dev.db"
$env:QUICK119_EXTERNAL_API = "0"
$env:QUICK119_LOAD_ENV = "0"
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

1. 다른 PowerShell 창에서 `ipconfig`를 실행해 현재 Wi-Fi 어댑터의 IPv4 주소를 확인합니다.
2. 예를 들어 PC 주소가 `192.168.0.10`이면 Android Chrome에서 `http://192.168.0.10:8000`을 엽니다. 스마트폰의 `localhost`는 PC를 가리키지 않습니다.
3. Windows 방화벽이 차단하면 개인 네트워크에서만 해당 Python/8000 포트의 연결을 허용합니다. 공유기의 기기 간 통신 차단·게스트 Wi-Fi도 확인합니다. 방화벽 전체를 끄거나 공유기 외부 포트 포워딩을 하지 않습니다.
4. 합성 데이터로 너비·스크롤·터치·키보드·페이지 이동을 확인합니다. 현재 환자 경로에는 인증이 없으므로 실제 개인정보는 입력하지 않습니다.
5. PC의 LAN IP로 접속한 HTTP는 보안 컨텍스트가 아닙니다. 마이크·GPS·Service Worker·PWA 설치 검증은 다음 HTTPS 절차에서 수행합니다. HTTP 화면 접속 성공을 설치/권한 검증 완료로 기록하지 않습니다.
6. 테스트 후 서버를 Ctrl+C로 종료합니다.

### HTTPS에서 실제 Android PWA를 설치하는 방법

Manifest·아이콘·Service Worker는 구현됐으며 아래는 공개 HTTPS 스테이징에서 확인할 절차입니다. 공개 전에 접근 제어를 구현·검증해야 합니다. 사설망의 합성 데이터 HTTPS 테스트는 `ANDROID_PWA.md`를 참고하세요.

1. 별도의 합성 데이터 DB를 사용하는 HTTPS 스테이징 서버를 준비합니다. 기존 `emergency.db`와 `.env`를 복사·업로드하지 않습니다. 실제 병원 API는 별도 허가 전까지 비활성화합니다.
2. 신뢰되는 인증서가 있는 도메인과 HTTPS 리버스 프록시를 FastAPI에 연결합니다. Uvicorn은 내부 주소로 제공하고 실제 휴대폰은 도메인의 HTTPS URL에 접속합니다. 인증서 경고를 무시하거나 브라우저 보안을 끄는 방식은 사용하지 않습니다.
3. 공개 인터넷에 서버 또는 PC 터널을 노출하기 전에 아래 환자 접근 권한·HTTPS 점검을 통과해야 합니다. 현재 인증 없는 개발 서버를 그대로 공개하지 않습니다.
4. Android Chrome에서 해당 HTTPS 주소를 열고 인증서·Manifest·아이콘 응답·Service Worker 등록/제어 범위를 확인합니다. 휴대폰의 실제 기기·OS·Chrome 버전을 기록합니다.
5. Chrome 메뉴의 `홈 화면에 추가` 또는 `앱 설치` 항목으로 설치합니다. 표시 문구는 버전에 따라 다릅니다. 앱 내 설치 버튼은 `beforeinstallprompt`가 제공되는 경우에만 노출하고, 이벤트가 없으면 메뉴 설치 안내를 제공합니다.
6. 홈 화면의 퀵119 아이콘으로 다시 실행해 standalone 화면, 올바른 시작 URL과 범위, 재실행과 뒤로가기를 확인합니다. 단순 바로가기 생성과 설치형 PWA 실행을 구분해 기록합니다.
7. 설치 앱에서도 마이크·GPS의 허용/거부/재요청·미지원 상태와 직접 입력 대체, 화면 잠금·복귀 후 동작을 확인합니다. HTTPS와 권한 허용만으로 음성 서비스 정상 작동을 단정하지 않습니다.
8. 오프라인 전환 및 앱 업데이트 후에도 환자·위치·응급실 응답이 Cache Storage에 남지 않고 과거 병상 정보를 표시하지 않는지 검사합니다. 설치 삭제/재설치도 확인합니다.

HTTPS 스테이징 주소와 설치 가능한 결과물은 이후 실제 구현·배포 결과로 기록합니다. 이번 작업에서 배포·터널 생성·인증서 설치·실기기 검증을 수행하지 않았습니다.

### 실제 서버 배포 전 점검 항목

- 로그인·세션과 환자별 접근 권한을 서버에서 검사합니다. 다른 사용자 환자 ID를 URL에 넣어도 목록·상세·수정·삭제에 접근할 수 없어야 합니다. 등록/삭제 같은 변경 요청의 CSRF 방어도 확인합니다.
- HTTPS 인증서·HTTP→HTTPS 전환·혼합 콘텐츠를 확인합니다. 리버스 프록시의 전달 헤더는 신뢰할 프록시에서만 수용하고 외부 HTTPS URL로 자원을 생성해야 합니다.
- 세션 쿠키의 Secure·HttpOnly·SameSite 설정, 필요한 출처만 허용하는 CORS·권한 정책, 민감 응답의 no-store를 검증합니다. CSP 등 보안 헤더는 기존 기능이 동작하는지 함께 확인합니다.
- DB·백업·인증키·로그를 정적 파일이나 오류 응답으로 노출하지 않습니다. 로그/URL에 환자·증상·좌표가 남는 현재 구조의 개선도 배포 전에 완료합니다.
- 합성 데이터로 사용자 간 접근 차단·미로그인 접근 거부·로그아웃 후 접근·오프라인 캐시 미저장을 검증합니다. 이 점검은 아직 미완료이며 현재 서버의 공개 배포를 안전하다고 판단한 상태가 아닙니다.

### 검증 기록 원칙

자동 테스트 / PC Chrome 반응형 에뮬레이션 / Android Chrome 실기기 / 설치 PWA 실기기를 구분해 기록합니다.
실기기 결과에는 날짜, 기기·Android·Chrome 버전, HTTPS 환경, 합성 데이터 사용, 재현 단계, 기대/실제 결과, 통과·실패·미실행을 남깁니다. PC 테스트나 테스트 코드 통과만으로 Android 설치·음성·GPS 검증 완료라고 표시하지 않습니다.
기준 문서화 당시의 테스트 결과는 16개 통과였습니다. 이후 PWA 코드 변경의 최신 결과는 아래 기록을 참고하세요. 모든 Android 실기기 항목은 미검증입니다.

## 후속 변경: 응급 접수에서 음성 문진 우선

- 사용자 요청에 따라 홈의 `응급상황 접수 시작` → `/emergency` → `/voice-intake` → 결과 확인 → `/emergency?step=patient` → 등록/미등록 환자 선택 순서로 변경했습니다.
- `/emergency`는 음성 문진으로 303 리다이렉트합니다. 환자 선택은 `step=patient`에서 제공해 문진 완료 후 다시 음성 화면으로 돌아가는 반복 이동을 방지합니다.
- 음성인식이 지원되지 않거나 마이크를 사용할 수 없으면 기존 직접 입력 기능을 사용할 수 있습니다. 마이크는 사용자 버튼 조작으로 시작합니다.
- 변경 파일: `main.py`, `templates/emergency.html`, `templates/voice_intake.html`, `tests/test_app.py`, 이 문서.
- 음성 화면의 다음 링크를 Jinja2로 렌더링하고 안내 문구를 수정했으며, 음성인식·추출 로직과 DB 스키마는 유지했습니다.
- 후속 자동 테스트: `python -m unittest discover -s tests -v` 총 16개 통과. 응급 접수 리다이렉트, 문진 다음 링크, 환자 선택 진입과 기존 회귀 테스트를 확인했습니다. 브라우저·실기기 음성 조작은 여전히 미검증입니다.

## 이번 작업 범위

기존 Python / FastAPI / SQLite / HTML / CSS / JavaScript를 유지했습니다.
안전한 개발·테스트 환경, 모바일 홈, 응급상황 접수 선택만 구현했습니다.
화면 기준은 `design/quick119-ui.png`이며 메인 색상은 #17375E, 응급 색상은 #E5484D입니다.
실제 신고·전송, PWA 설치, 채팅, 음성 메시지, 관리자, 길찾기는 이번에 구현하지 않았습니다.

## 데이터·인증키 보호

- 작업 및 테스트에서 기존 `emergency.db`는 열거나 수정하거나 삭제하지 않았습니다.
- 실제 `.env`는 열거나 출력하지 않았습니다. 인증키를 외부로 전송하지 않았습니다.
- 기본 실행 모드는 `development`이며 별도 `data/quick119-dev.db`를 사용합니다.
- `test` 모드는 명시적인 `QUICK119_DB_PATH`가 필수이며 실제 테스트는 OS 임시 폴더의 SQLite를 사용합니다.
- 개발·테스트에서는 프로젝트 루트 `emergency.db`를 DB 경로로 지정하면 초기 설정 단계에서 거부합니다.
- Python 모듈 import 시 `.env` 로드 및 DB 생성·연결을 수행하지 않습니다.
- DB 생성은 FastAPI lifespan 시작 시 수행합니다. 기존 환자 스키마와 같은 `CREATE TABLE IF NOT EXISTS`만 사용하며 초기화·데이터 삭제는 하지 않습니다.
- 환자 삭제도 공통 DB 설정을 사용합니다. 기존 DB 경로를 직접 사용하던 코드를 제거했습니다.
- 개발·테스트의 외부 API 호출은 차단합니다. 키가 있거나 허용 플래그가 1이어도 테스트 모드에서는 호출하지 않습니다. 가짜 병원 데이터를 실제 결과처럼 보여주지 않습니다.
- 운영 기능은 보존했습니다. 운영은 명시적인 설정이 필요하며 이번 작업에서 실행하거나 검증하지 않았습니다.

## 실행 설정

| 변수 | 의미 |
| --- | --- |
| `QUICK119_ENV` | `development`(기본), `test`, `production` |
| `QUICK119_DB_PATH` | 사용할 SQLite 경로. 상대 경로는 프로젝트 루트 기준. test에서는 필수 |
| `QUICK119_EXTERNAL_API` | production이면서 `1`일 때만 기존 외부 API 허용. 기본 0 |
| `QUICK119_LOAD_ENV` | production이면서 `1`일 때만 lifespan에서 실제 `.env` 로드. 기본 0 |

운영 기본 DB는 기존 `emergency.db`이므로, 운영 모드를 시험 목적으로 켜지 마세요.
`QUICK119_ENV`, `QUICK119_DB_PATH`는 Python import 전에 프로세스 환경변수로 지정해야 합니다.
`.env`는 운영 시작 단계에서 선택적으로 읽으므로 위 두 설정을 `.env`만으로 변경할 수 없습니다.
`.env` 값은 이미 존재하는 프로세스 환경변수를 덮어쓰지 않습니다.
운영 모드와 외부 API 사용은 사용자의 별도 지시 후에만 검증하세요.

## 현재 구현된 기능

- 새 모바일 홈: 로고, 네이비 배경, 큰 응급 접수 버튼, 음성 문진·환자 목록·응급실 바로가기, 하단 메뉴.
- 병원 채팅은 비활성 버튼과 `추후 지원 예정` 문구로 표시.
- 새 접수 선택: 등록 환자 선택 후 기존 GET 폼 제출, 환자가 없으면 등록 안내, 미등록 환자 바로 진행, 음성 문진 연결.
- 이전 음성 문진 요약 표시와 명시적 삭제. 기존 `emergencyVoiceIntake` 저장 형식 유지.
- 새 두 화면은 Jinja2 렌더링과 모바일 공통 CSS/JS 사용. Jinja2 자동 이스케이프 적용.
- 기존 기능: 환자 등록·목록·상세·삭제, 의식·호흡·증상·통증 부위, 전달 요약, GPS·응급실 조회·펼침 상세 코드를 유지.
- 다른 접수 화면은 기존 HTML과 기존 CSS를 유지. CSS는 같은 내용의 `static/medical-ui.css`로 제공.
- `templates/`를 정적 공개하던 구조를 `static/`만 공개하도록 변경.

## 기존 URL과 폼 계약

| 메서드 | 경로 |
| --- | --- |
| GET | `/` |
| GET / POST | `/register` |
| GET | `/patients` |
| GET | `/patients/{patient_id}` |
| POST | `/patients/{patient_id}/delete` |
| GET | `/voice-intake` |
| GET | `/emergency` |
| GET | `/emergency/check` |
| GET | `/emergency/breathing` |
| GET | `/emergency/symptoms` |
| GET | `/emergency/bodymap` |
| GET | `/emergency/report` |
| GET | `/emergency/hospitals` |

등록 환자 폼은 `/emergency/check`에 `patient_id`를 GET으로 보냅니다.
미등록 환자는 `/emergency/check?patient_id=0`으로 이동합니다.
의식·호흡·증상·부위 값은 기존 쿼리/숨김 입력 흐름을 유지합니다.
DB 환자 컬럼 순서는 `id, name, birth, phone, address, guardian, guardian_phone` 그대로입니다.

## 수정·생성 파일

수정:
- `main.py`: lifespan 초기화, 두 화면 Jinja2 렌더링, 새 static 연결, 신규 화면의 기존 테마 중복 삽입 제외, 삭제 DB 경로 통일·연결 종료, 외부 API 차단, 좌표 정확도 누락과 미확인 상태 처리.
- `database.py`: DB 경로 공통 설정 사용, import 시 자동 생성 제거.
- `api_integrations.py`: 외부 API 네트워크 진입점 및 키 사용 가드.
- `templates/index.html`: 시안 기반 모바일 홈.
- `.gitignore`: `.env`, DB 및 SQLite 부속 파일, 가상환경, 캐시, 로그 제외.

생성:
- `settings.py`: 실행 모드, DB 경로 보호, 외부 API 정책.
- `requirements.txt`: 확인된 직접 의존성 버전. HTTP 회귀 테스트용 httpx 포함.
- `templates/base.html`, `templates/components.html`: 새 화면 공통 레이아웃과 SVG 아이콘 매크로.
- `templates/emergency.html`: 접수 선택 화면.
- `static/quick119.css`, `static/quick119.js`, `static/icons.svg`: 새 화면 스타일, 이전 음성 정보 표시·삭제, 아이콘.
- `static/medical-ui.css`: 기존 화면의 스타일을 바이트 그대로 복사. 원본은 비교와 추후 정리를 위해 유지.
- `tests/test_app.py`: 별도 임시 DB·네트워크 차단 회귀 테스트.
- `PROJECT_STATUS.md`: 이 문서.

초기 작업에서는 `templates/register.html`, `templates/voice_intake.html`, `templates/medical-ui.css`, UI 시안을 수정하지 않았습니다. 이후 음성 우선 흐름 변경으로 `templates/voice_intake.html`의 안내와 다음 링크만 수정했습니다.

## 검증 결과

실행 명령:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

초기 결과: 자동 테스트 15개 모두 통과. 음성 우선 흐름 추가 후 직전 결과는 16개 통과입니다. `pip check`: `No broken requirements found.`

검증 내용:
- import 시 모든 SQLite 연결과 실제 `.env` 읽기를 차단한 상태에서 앱 import 성공.
- 테스트 전 구간에서 임시 DB 이외의 SQLite 연결은 예외 처리.
- 외부 `urllib.request.urlopen`을 차단하고 호출되지 않음을 확인.
- 기존 14개 URL·메서드 조합 유지, 기존 화면들의 HTTP 응답 확인.
- 테스트 환자 등록·목록·상세, POST 삭제·리다이렉트, GET 삭제 금지, 존재하지 않는 환자 삭제 404 확인.
- 등록 환자 선택·이름 이스케이프, 빈 목록 안내, 미등록 접수 링크 확인.
- 의식·호흡·증상·부위·전달 요약의 서버 경로 및 전달 데이터 확인.
- 홈 한국어·바로가기·하단 메뉴·비활성 채팅·기존 헤더 중복 방지 확인.
- 좌표만 입력하고 accuracy를 생략한 병원 화면이 200 응답이며 정확도 미확인 표시.
- test 환경에서 외부 허용 플래그를 1로 해도 API 가드 유지.
- 새 정적 자원 4개 제공, 템플릿과 `.env`의 정적 URL 접근 불가.
- 기존 CSS 복사본 바이트 동일성 및 기존 환자 스키마 일치 확인.

테스트는 Starlette TestClient를 통한 인프로세스 HTTP 검증입니다. 실제 브라우저나 Uvicorn 서버를 사용한 검증은 아닙니다.

## 발견한 오류와 처리

- 삭제 라우트가 기존 DB 경로를 고정 사용하던 문제: 공통 DB 설정으로 변경.
- Windows에서 SQLite 컨텍스트 매니저만으로 연결이 닫히지 않는 문제: 삭제 라우트와 테스트에 `closing` 적용. 최초 테스트의 임시 폴더 정리 실패도 이후 해결.
- CSS 복사 시 줄바꿈 차이: 바이트 복사로 변경해 기존 스타일 원본 보존.
- `accuracy=None`에서 반올림 오류: 미확인 표시로 처리.
- 의식·호흡 값 누락을 위험 상태로 단정하던 병원 화면: 미확인 표시로 처리.
- 현재 설치된 Starlette는 TestClient의 httpx 사용에 deprecation 경고를 출력합니다. 테스트 실패는 없으며, 향후 테스트 클라이언트 의존성 정리 시 검토하세요. 경고를 숨기지 않았습니다.

## 미실행 검증과 남은 한계

- 실제 `.env`·기존 DB·실제 환자 데이터 확인: 사용자 지시에 따라 미실행.
- 실제 공공/심평원/카카오/역지오코딩 API: 개발·테스트에서 차단, 실제 연결 검증 미실행.
- 브라우저·실기기 화면·가로/세로·터치·하단 안전영역: 현재 브라우저 자동화/미리보기 도구가 없어 미검증. CSS와 HTTP 응답 기준으로만 확인.
- JavaScript 실행, sessionStorage 버튼 조작, GPS·마이크 권한: 브라우저 미검증.
- 초기에는 PWA 미구현이었고 현재는 Manifest·Service Worker·설치 UI를 구현했습니다. 실제 Android 설치 검증 완료 상태는 아닙니다.
- 기존 음성 추출은 정규식이며 문맥 이해 AI가 아닙니다. 이전 음성 결과의 환자/접수 식별자 연결은 아직 없고, 이번에는 확인 문구와 삭제 기능만 추가했습니다.
- 기존 문진 화면의 직접 HTML 삽입·인증/권한·쿼리로 의료정보 전달 문제는 별도 개선 필요.
- 기존 응급실 검색은 행정구역 제한·최대 30기관 기본정보 연결 후 직선거리 최대 5곳 방식입니다.
- 기존 카카오 이동시간 코드 등 2차 관련 코드는 삭제하지 않았지만 개발·테스트에서는 차단됩니다. 다음 단계에서도 활성화하지 마세요.

## Windows PowerShell 실행

프로젝트 루트에서 기존 가상환경을 사용합니다. 설치와 실행 명령은 다음과 같습니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:QUICK119_ENV = "development"
$env:QUICK119_DB_PATH = "data/quick119-dev.db"
$env:QUICK119_EXTERNAL_API = "0"
$env:QUICK119_LOAD_ENV = "0"
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

접속 주소: `http://127.0.0.1:8000`
첫 서버 시작 시 개발 DB만 생성합니다. 초기 환자 정보는 비어 있습니다. 개발 DB에 실제 개인정보를 입력하지 마세요.
위 수동 서버 실행 명령은 안내용입니다. 초기 작업에서는 서버를 시작하지 않았으며, 최신 PWA 검증에서는 임시 DB를 사용하는 loopback Uvicorn 서버만 테스트 중 실행한 뒤 종료했습니다.
자동 테스트 명령은 실행 모드와 임시 DB를 자체 설정하므로 위 개발용 환경변수와 독립적으로 테스트합니다.
새 가상환경이 필요하면 먼저 `py -3.13 -m venv .venv`로 생성하세요. 기존 환경은 덮어 만들지 마세요.

## 다음 개발 단계

1. 현재 음성 우선 흐름을 유지하면서 Android Chrome 기준 360~430px와 터치·키보드 UI를 확인·보완합니다. 같은 Wi-Fi HTTP는 화면 확인용으로 사용합니다.
2. 구현한 Manifest·192/512 PNG·루트 SW·설치/오프라인 안내를 사설망 HTTPS와 실제 Android에서 점검합니다. 정적 파일 변경 시 SW 캐시 버전을 갱신합니다.
3. 환자 입력/선택과 접수별 임시 상태를 설계하고 기존 스키마를 보존합니다. 나머지 문진 화면은 단계적으로 Jinja2로 옮겨 검증·이스케이프·이전 선택 복원을 적용합니다.
4. 음성 결과와 환자 연결, 직접 입력·권한 실패 처리, 요약 복사, 병원 목록·상세의 출처/오류 처리를 개선합니다. 실제 119 전송 성공 표시는 금지합니다.
5. 환자 접근 권한·민감 URL/로그·HTTPS 보안을 점검하고 합성 DB를 사용하는 HTTPS 스테이징을 준비합니다. 외부 공개 전에 접근 제어를 검증합니다.
6. 실제 Android Chrome과 설치 PWA에서 설치·재실행·360~430px UI·마이크·GPS·거부/미지원 대체·캐시 미저장·업데이트를 검증하고 결과를 기록합니다.

## Claude Code 이전 시

- 이 문서, `settings.py`, `tests/test_app.py`를 먼저 읽고 현재 UI 시안과 기존 라우트를 보존하세요.
- 이 문서의 Android 설치형 PWA 필수 기준을 개발 완료 조건으로 적용하세요. 현재 음성 우선 흐름을 유지하며, 실제 Android 검증 전에는 설치·마이크·GPS 동작을 검증 완료로 표시하지 마세요.
- `.env`, `emergency.db`는 열지 말고 DB 초기화·삭제·운영 모드 실행을 하지 마세요.
- 테스트는 `python -m unittest discover -s tests -v`를 기준으로 하고 실제 외부 API를 허용하지 마세요.
- 실행 기준은 현재 Python 3.13 가상환경입니다. 의존성은 `requirements.txt`로 설치합니다.
- 새 화면은 `base.html`/`components.html`과 `quick119.css`를 사용하며 기존 테마 미들웨어에서 제외됩니다.
- 기존 화면은 여전히 Python HTML 문자열과 `medical-ui.css`를 사용합니다. 한 번에 전면 교체하지 마세요.
- 테스트 환자 이름·연락처는 합성 값이며 운영 데이터가 아닙니다. 테스트는 임시 DB를 종료 후 정리합니다.
- 미검증 항목을 정상 동작한다고 기록하지 말고, 변경 파일·실행 테스트·남은 검증을 계속 갱신하세요.
