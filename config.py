from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ruc_auth import DEFAULT_RUC_ASSERT_URL, DEFAULT_RUC_OAUTH_AUTHORIZE_URL


DEFAULT_RUC_GRADES_URL = (
    "https://jw.ruc.edu.cn/resService/jwxtpt/v1/xsd/cjgl_xsxdsq/findKccjList"
    "?resourceCode=XSMH0526"
    "&apiCode=jw.xsd.xsdInfo.controller.CjglKccjckController.findKccjList"
)

DEFAULT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)


def load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


def _get_list(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    ruc_token: str
    pushplus_token: str
    grade_hash_salt: str
    ruc_username: str = ""
    ruc_password: str = ""
    ruc_cookie: str = ""
    ruc_grades_url: str = DEFAULT_RUC_GRADES_URL
    ruc_assert_url: str = DEFAULT_RUC_ASSERT_URL
    ruc_oauth_authorize_url: str = DEFAULT_RUC_OAUTH_AUTHORIZE_URL
    ruc_login_school_code: str = "ruc"
    ruc_user_role_code: str = "student"
    ruc_user_agent: str = ""
    ruc_browser_user_agent: str = DEFAULT_BROWSER_USER_AGENT
    ruc_page_size: int = 100
    ruc_plan_type: str = "1"
    ruc_semesters: list[str] | None = None
    state_file: Path = Path("seen_grades.json")
    pushplus_url: str = "https://www.pushplus.plus/send"
    pushplus_template: str = "html"
    baseline_notify: bool = False
    notify_unchanged: bool = False
    request_timeout: int = 30
    token_expiry_warning_seconds: int = 0


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        ruc_token=os.getenv("RUC_TOKEN", "").strip(),
        ruc_username=os.getenv("RUC_USERNAME", "").strip(),
        ruc_password=os.getenv("RUC_PASSWORD", "").strip(),
        ruc_cookie=os.getenv("RUC_COOKIE", "").strip(),
        ruc_grades_url=os.getenv("RUC_GRADES_URL", DEFAULT_RUC_GRADES_URL).strip(),
        ruc_assert_url=os.getenv("RUC_ASSERT_URL", DEFAULT_RUC_ASSERT_URL).strip(),
        ruc_oauth_authorize_url=os.getenv(
            "RUC_OAUTH_AUTHORIZE_URL",
            DEFAULT_RUC_OAUTH_AUTHORIZE_URL,
        ).strip(),
        ruc_login_school_code=os.getenv("RUC_LOGIN_SCHOOL_CODE", "ruc").strip(),
        ruc_user_role_code=os.getenv("RUC_USER_ROLE_CODE", "student").strip(),
        ruc_user_agent=os.getenv("RUC_USER_AGENT", "").strip(),
        ruc_browser_user_agent=(
            os.getenv("RUC_BROWSER_USER_AGENT", "").strip()
            or DEFAULT_BROWSER_USER_AGENT
        ),
        ruc_page_size=_get_int("RUC_PAGE_SIZE", 100),
        ruc_plan_type=os.getenv("RUC_PLAN_TYPE", "1").strip(),
        ruc_semesters=_get_list("RUC_SEMESTERS"),
        pushplus_token=os.getenv("PUSHPLUS_TOKEN", "").strip(),
        pushplus_url=os.getenv("PUSHPLUS_URL", "https://www.pushplus.plus/send").strip(),
        pushplus_template=os.getenv("PUSHPLUS_TEMPLATE", "html").strip(),
        grade_hash_salt=os.getenv("GRADE_HASH_SALT", "").strip(),
        state_file=Path(os.getenv("STATE_FILE", "seen_grades.json")),
        baseline_notify=_get_bool("BASELINE_NOTIFY", False),
        notify_unchanged=_get_bool("NOTIFY_UNCHANGED", False),
        request_timeout=_get_int("REQUEST_TIMEOUT", 30),
        token_expiry_warning_seconds=_get_int(
            "TOKEN_EXPIRY_WARNING_SECONDS", 0
        ),
    )
