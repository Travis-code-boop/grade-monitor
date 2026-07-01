from __future__ import annotations

import http.cookiejar
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

if TYPE_CHECKING:
    from config import Settings


DEFAULT_RUC_ASSERT_URL = (
    "https://jw.ruc.edu.cn/secService/assert.json"
    "?resourceCode=resourceCode"
    "&apiCode=framework.sign.controller.SignController.asserts"
)

DEFAULT_RUC_OAUTH_AUTHORIZE_URL = (
    "https://v.ruc.edu.cn/oauth2/authorize"
    "?response_type=code"
    "&scope=all"
    "&state=yourstate"
    "&client_id=5d25ae5b90f4d14aa601ede8.ruc"
    "&redirect_uri=https://jw.ruc.edu.cn/secService/oauthlogin"
)


class RucPasswordLoginError(RuntimeError):
    pass


@dataclass(frozen=True)
class RucPasswordSession:
    token: str
    cookie_header: str


@dataclass(frozen=True)
class HttpTextResponse:
    status: int
    url: str
    text: str
    headers: Any


class LoginIframeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.iframe_src = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "iframe":
            return
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if attr_map.get("id") == "login-iframe" or not self.iframe_src:
            self.iframe_src = attr_map.get("src", "")


class CsrfTokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.token = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if attr_map.get("name") == "csrftoken" or attr_map.get("id") == "csrftoken":
            self.token = attr_map.get("value", "")


def login_with_password(settings: Settings) -> RucPasswordSession:
    if not settings.ruc_username or not settings.ruc_password:
        raise RucPasswordLoginError("RUC_USERNAME/RUC_PASSWORD is not configured")

    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))

    service_url = _discover_oauth_service(opener, settings)
    account_page = _request_text(opener, service_url, settings.request_timeout)
    iframe_url = extract_login_iframe_url(account_page.text, account_page.url)
    login_page = _request_text(opener, iframe_url, settings.request_timeout)
    csrf_token = extract_csrf_token(login_page.text)
    redirect_uri = raw_query_value(login_page.url, "redirect_uri") or raw_query_value(
        iframe_url, "redirect_uri"
    )
    if not csrf_token:
        raise RucPasswordLoginError("统一身份认证登录页未返回 CSRF token")
    if not redirect_uri:
        raise RucPasswordLoginError("统一身份认证登录页未返回 redirect_uri")

    login_result = _submit_password_login(
        opener,
        login_page.url,
        settings,
        csrf_token,
        redirect_uri,
    )
    final_response = _request_text(
        opener,
        login_result["redirect_uri"],
        settings.request_timeout,
    )

    token = cookie_value(cookie_jar, "token", "jw.ruc.edu.cn")
    if not token:
        token = extract_token_from_json(final_response.text)
    if not token:
        assert_response = _request_text(
            opener,
            settings.ruc_assert_url,
            settings.request_timeout,
        )
        token = cookie_value(cookie_jar, "token", "jw.ruc.edu.cn")
        if not token:
            token = extract_token_from_json(assert_response.text)

    if not token:
        raise RucPasswordLoginError("账号密码登录成功后未拿到教务 TOKEN")

    cookie_header = cookie_header_for(cookie_jar, "jw.ruc.edu.cn")
    return RucPasswordSession(token=token, cookie_header=cookie_header)


def format_vruc_username(username: str, school_code: str = "ruc") -> str:
    value = username.strip()
    if not value:
        return ""
    if "@" in value or ":" in value:
        return value
    if re.fullmatch(r"1\d{10}", value):
        return "%2B86 " + value
    if not school_code:
        return value
    return f"{school_code}:{value}"


def extract_login_iframe_url(html: str, base_url: str) -> str:
    parser = LoginIframeParser()
    parser.feed(html)
    if parser.iframe_src:
        return urljoin(base_url, parser.iframe_src)
    if "/auth/login" in urlsplit(base_url).path:
        return base_url
    raise RucPasswordLoginError("统一身份认证页面未找到登录 iframe")


def extract_csrf_token(html: str) -> str:
    parser = CsrfTokenParser()
    parser.feed(html)
    return parser.token


def raw_query_value(url: str, name: str) -> str:
    for part in urlsplit(url).query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        if key == name:
            return value
    return ""


def describe_login_error(error_description: str) -> str:
    if error_description == "verification failed":
        return "统一身份认证账号或密码不正确"
    if error_description == "captcha error":
        return "统一身份认证要求图片验证码，GitHub Actions 无法自动完成"
    if error_description == "need twofactor":
        return "统一身份认证要求二次验证码，GitHub Actions 无法自动完成"
    if error_description == "the user is not found":
        return "统一身份认证用户不存在"
    if error_description:
        return f"统一身份认证失败: {error_description}"
    return "统一身份认证失败"


def cookie_value(
    cookie_jar: http.cookiejar.CookieJar,
    name: str,
    host: str,
) -> str:
    for cookie in cookie_jar:
        if cookie.name == name and _cookie_domain_matches(cookie.domain, host):
            return cookie.value
    return ""


def cookie_header_for(cookie_jar: http.cookiejar.CookieJar, host: str) -> str:
    pairs = [
        f"{cookie.name}={cookie.value}"
        for cookie in cookie_jar
        if _cookie_domain_matches(cookie.domain, host)
    ]
    return "; ".join(pairs)


def extract_token_from_json(text: str) -> str:
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return ""
    return _find_token(body)


def _discover_oauth_service(opener, settings: Settings) -> str:
    response = _request_text(opener, settings.ruc_assert_url, settings.request_timeout)
    body = _decode_json_object(response.text)
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict) and data.get("service"):
        return str(data["service"])
    return settings.ruc_oauth_authorize_url


def _submit_password_login(
    opener,
    login_page_url: str,
    settings: Settings,
    csrf_token: str,
    redirect_uri: str,
) -> dict[str, Any]:
    login_url = urljoin(login_page_url, "/auth/login")
    payload = {
        "username": format_vruc_username(
            settings.ruc_username,
            settings.ruc_login_school_code,
        ),
        "password": settings.ruc_password,
        "code": "",
        "remember_me": "false",
        "redirect_uri": redirect_uri,
        "twofactor_password": "",
        "twofactor_recovery": "",
        "token": csrf_token,
        "captcha_id": "",
    }
    response = _request_text(
        opener,
        login_url,
        settings.request_timeout,
        method="POST",
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": login_page_url,
        },
    )
    body = _decode_json_object(response.text)
    if response.status < 200 or response.status >= 300:
        error_description = str(body.get("error_description", "")).strip()
        raise RucPasswordLoginError(describe_login_error(error_description))
    redirect = body.get("redirect_uri")
    if not redirect:
        raise RucPasswordLoginError("统一身份认证登录成功后未返回 redirect_uri")
    return {"redirect_uri": str(redirect)}


def _request_text(
    opener,
    url: str,
    timeout: int,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> HttpTextResponse:
    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
    }
    if headers:
        request_headers.update(headers)
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            return HttpTextResponse(
                status=response.status,
                url=response.geturl(),
                text=text,
                headers=response.headers,
            )
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return HttpTextResponse(
            status=exc.code,
            url=exc.geturl(),
            text=text,
            headers=exc.headers,
        )
    except URLError as exc:
        raise RucPasswordLoginError(f"统一身份认证网络请求失败: {exc}") from exc


def _decode_json_object(text: str) -> dict[str, Any]:
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return body if isinstance(body, dict) else {}


def _find_token(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("TOKEN", "token", "access_token"):
            token = value.get(key)
            if isinstance(token, str) and token:
                return token
        for nested in value.values():
            token = _find_token(nested)
            if token:
                return token
    if isinstance(value, list):
        for item in value:
            token = _find_token(item)
            if token:
                return token
    return ""


def _cookie_domain_matches(cookie_domain: str, host: str) -> bool:
    domain = cookie_domain.lstrip(".")
    return host == domain or host.endswith("." + domain)
