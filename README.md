# Quick 119

구급대원 모바일 PWA에서 가상 환자 정보를 접수하고 병원에 수용을 문의합니다. 병원 웹에서 회신하면 구급대가 목적지를 확정하고 이송·도착 상태와 대화를 공유합니다.

**이 저장소는 인증 없는 가상 데이터 시연용입니다. 실제 환자 정보나 임상 판단에 사용하지 마세요.**

## 구성

- `quick119-pwa/`: FastAPI 백엔드, 구급대 PWA, 공통 서버 연결 및 실행 스크립트
- `hospital-ui/`: 병원 웹
- `server/`: 공통 API, SQLite, 음성 접수, 문의 및 배정
- `config/`, `datasets/demo/`: 실행 설정과 서울 시연 자료
- `simulation/observed_inquiry.py`, `solver_improved.py`, `speech_eval/asr_correction.py`: 공통 서버에서 사용하는 문의·배정·음성 처리
- `tests/`, `quick119-pwa/tests/`: 유지된 기능의 회귀 테스트

## 로컬 시연

Windows PowerShell에서 Python을 설치하고 저장소 루트에서 시작합니다.

```powershell
cd quick119-pwa
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`quick119-pwa`에서 터미널 두 개를 사용합니다.

```powershell
# 터미널 1: 공통 서버
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-team-chat.ps1

# 터미널 2: PWA 백엔드 (가상 직선 경로)
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-chat-api.ps1 -SyntheticRoutes
```

기본 주소는 [구급대 화면](http://127.0.0.1:18034/transport/)과 [병원 화면](http://127.0.0.1:18034/hospital/)입니다. 포트가 이미 사용 중이면 `-Port`와 `-TeamPort`를 함께 조정하세요. 각 서버는 해당 터미널에서 Ctrl+C로 종료합니다. 별도의 가상 DB는 `quick119-pwa/.local/`에 저장되며 기존 DB를 삭제하지 않습니다.

공통 서버는 `voice-only` 모드로 실행되므로 자동 문의·배정 워커는 꺼져 있습니다. 직선 경로와 시간은 가상 시연일 뿐 실제 경로나 이송 판단에 쓸 수 없습니다. 상세 화면 사용법은 [이송 화면 안내](quick119-pwa/TRANSPORT_UI.md)를 참고하세요.

## 검증

저장소 루트에서 실행합니다.

```powershell
.\quick119-pwa\.venv\Scripts\python.exe -m pip install -r requirements\requirements-server.txt
.\quick119-pwa\.venv\Scripts\python.exe -m unittest discover -s tests\core
Push-Location quick119-pwa
$env:PYTHONPATH = ".;.."
.\.venv\Scripts\python.exe -m unittest discover -s tests
node --test tests\test_chat_intake.mjs
Pop-Location
```

브라우저 통합 테스트에는 Chrome이 필요합니다. `full` 모드의 고정 실행 프로필은 특정 Python·SQLite 버전을 요구하며, 환경이 다를 때는 명시적 호환 옵션이 필요합니다.
