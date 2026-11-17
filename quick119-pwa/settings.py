"""실제 환자 DB와 외부 서비스를 명시적으로 선택하는 실행 설정."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
APP_ENV = os.getenv("QUICK119_ENV", "development").lower()
if APP_ENV not in {"development", "test", "production"}:
    raise ValueError("QUICK119_ENV는 development, test, production 중 하나여야 합니다.")

_default_db = "emergency.db" if APP_ENV == "production" else "data/quick119-dev.db"
_configured_db = os.getenv("QUICK119_DB_PATH")
if APP_ENV == "test" and not _configured_db:
    raise ValueError("테스트 모드에서는 QUICK119_DB_PATH에 별도 DB를 지정하세요.")
DB_PATH = Path(_configured_db or _default_db)
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH
DB_PATH = DB_PATH.resolve()
if APP_ENV != "production" and DB_PATH == (BASE_DIR / "emergency.db").resolve():
    raise ValueError("개발/테스트 모드에서는 기존 emergency.db를 사용할 수 없습니다.")


def external_api_enabled():
    # 테스트 모드에서는 플래그나 기존 인증키가 있어도 네트워크를 허용하지 않습니다.
    return APP_ENV in {"development", "production"} and os.getenv("QUICK119_EXTERNAL_API", "0") == "1"


def require_external_api():
    if not external_api_enabled():
        raise RuntimeError("현재 실행 모드에서는 외부 API 호출이 비활성화되어 있습니다.")


def kakao_rest_api_key():
    """Read at call time: .env is loaded during application startup, never import."""
    if not external_api_enabled():
        return ""
    value = os.getenv("KAKAO_REST_API_KEY", "").strip()
    return value if value and not value.startswith("발급") else ""
