from __future__ import annotations

import argparse
from datetime import datetime
import sys
import unittest

from config import load_settings
from grade_diff import find_new_grades, fingerprint_set, normalize_grade_rows
from notifier import NotifyError, PushPlusNotifier, render_new_grades, render_plain
from ruc_jw_client import (
    RucAuthError,
    RucJwClient,
    RucJwError,
    RucResponseError,
    RucTokenExpired,
    is_network_error,
    parse_token,
)
from state_store import load_state, save_state


class HealthCheckError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor RUC JW grade updates.")
    parser.add_argument("--dry-run", action="store_true", help="Do not notify or save state.")
    parser.add_argument("--notify-test", action="store_true", help="Send a PushPlus test message.")
    parser.add_argument("--baseline-notify", action="store_true", help="Notify on first baseline run.")
    parser.add_argument("--config-check", action="store_true", help="Print a redacted local config check.")
    parser.add_argument("--health-check", action="store_true", help="Run tests, query grades, and notify health status.")
    args = parser.parse_args()

    settings = load_settings()
    notifier = PushPlusNotifier(settings)

    if args.config_check:
        print_config_check(settings)
        return 0

    if args.notify_test:
        try:
            notifier.send("成绩提醒测试", render_plain("PushPlus 通道已配置成功。"))
            print("PushPlus test notification sent.")
            return 0
        except NotifyError as exc:
            print(f"PushPlus test failed: {exc}", file=sys.stderr)
            return 1

    if args.health_check:
        return run_health_check(settings, notifier)

    try:
        client = RucJwClient(settings)
        if not args.dry_run:
            warn_if_token_near_expiry(
                client,
                notifier,
                settings.token_expiry_warning_seconds,
            )
        rows = client.fetch_undergraduate_grades()
        grades = normalize_grade_rows(rows)
        state = load_state(settings.state_file)
        current_fingerprints = fingerprint_set(grades, settings.grade_hash_salt)
        new_grades = find_new_grades(
            grades,
            state.fingerprints,
            settings.grade_hash_salt,
        )

        if state.first_run:
            print(f"Baseline created with {len(grades)} visible grades.")
            if (settings.baseline_notify or args.baseline_notify) and grades and not args.dry_run:
                notifier.send("成绩提醒基线已建立", render_new_grades(grades))
            if not args.dry_run:
                save_state(settings.state_file, current_fingerprints)
            return 0

        if not new_grades:
            print(f"No grade changes. Visible grades: {len(grades)}.")
            if settings.notify_unchanged and not args.dry_run:
                notifier.send("成绩提醒运行正常", render_plain(f"暂无新成绩，共 {len(grades)} 条。"))
            return 0

        print(f"Found {len(new_grades)} new or changed grades.")
        for grade in new_grades:
            print(f"- {grade.display_line()}")
        if not args.dry_run:
            notifier.send("新成绩出来了", render_new_grades(new_grades))
            save_state(
                settings.state_file,
                state.fingerprints | current_fingerprints,
                state.created_at,
            )
        return 0
    except RucTokenExpired as exc:
        return notify_failure(notifier, "教务 TOKEN 已过期", str(exc), not args.dry_run)
    except RucAuthError as exc:
        return notify_failure(notifier, "教务登录态失效", str(exc), not args.dry_run)
    except RucResponseError as exc:
        return notify_failure(notifier, "教务成绩接口异常", str(exc), not args.dry_run)
    except RucJwError as exc:
        return notify_failure(notifier, "教务查询失败", str(exc), not args.dry_run)
    except Exception as exc:
        if is_network_error(exc):
            return notify_failure(notifier, "教务网络请求失败", str(exc), not args.dry_run)
        raise


def warn_if_token_near_expiry(
    client: RucJwClient,
    notifier: PushPlusNotifier,
    warning_seconds: int,
) -> None:
    if warning_seconds <= 0:
        return
    token_info = client.token_info()
    seconds = token_info.seconds_until_expiry
    if seconds is not None and 0 < seconds <= warning_seconds:
        hours = max(1, seconds // 3600)
        notifier.send("教务 TOKEN 即将过期", render_plain(f"预计 {hours} 小时内过期，请准备更新。"))


def run_health_check(settings, notifier: PushPlusNotifier) -> int:
    try:
        test_count = run_unit_tests()
        client = RucJwClient(settings)
        rows = client.fetch_undergraduate_grades()
        grades = normalize_grade_rows(rows)
        if not grades:
            raise HealthCheckError("教务接口可访问，但没有解析到可见成绩。")

        message = (
            "成绩提醒健康检查正常。"
            f"单元测试 {test_count} 个通过，教务查询正常，可见成绩 {len(grades)} 条，"
            "PushPlus 通道正常。"
        )
        notifier.send("成绩提醒健康检查正常", render_plain(message))
        print(message)
        return 0
    except NotifyError as exc:
        print(f"成绩提醒健康检查失败: PushPlus 通道无法发送消息: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        reason = health_failure_message(exc)
        print(f"成绩提醒健康检查失败: {reason}", file=sys.stderr)
        try:
            notifier.send("成绩提醒健康检查失败", render_plain(reason))
        except Exception as notify_exc:
            print(f"Failed to send health failure notification: {notify_exc}", file=sys.stderr)
        return 1


def run_unit_tests() -> int:
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise HealthCheckError(
            f"代码自检未通过：失败 {len(result.failures)} 个，错误 {len(result.errors)} 个。"
        )
    return result.testsRun


def notify_failure(
    notifier: PushPlusNotifier,
    title: str,
    message: str,
    do_notify: bool = True,
) -> int:
    print(f"{title}: {message}", file=sys.stderr)
    if not do_notify:
        return 1
    try:
        notifier.send(safe_failure_title(title), render_plain(safe_failure_message(title)))
    except Exception as notify_exc:
        print(f"Failed to send failure notification: {notify_exc}", file=sys.stderr)
    return 1


def safe_failure_title(title: str) -> str:
    if "登录态" in title or "TOKEN" in title:
        return "教务登录失效"
    return title


def safe_failure_message(title: str) -> str:
    if "登录态" in title or "TOKEN" in title:
        return "教务登录失败，请检查账号密码配置，或确认统一身份认证没有要求验证码、二次验证。"
    if "接口" in title:
        return "教务成绩接口返回异常，请查看 GitHub Actions 日志。"
    if "网络" in title:
        return "教务网络请求失败，请查看 GitHub Actions 日志。"
    return "成绩监控运行失败，请查看 GitHub Actions 日志。"


def health_failure_message(exc: BaseException) -> str:
    if isinstance(exc, (RucTokenExpired, RucAuthError)):
        return "教务登录失败，请检查账号密码配置，或确认统一身份认证没有要求验证码、二次验证。"
    if isinstance(exc, RucResponseError):
        return "教务成绩接口返回异常，请查看 GitHub Actions 日志。"
    if isinstance(exc, RucJwError):
        return "教务查询失败，请查看 GitHub Actions 日志。"
    if is_network_error(exc):
        return "网络请求失败，请查看 GitHub Actions 日志。"
    if isinstance(exc, HealthCheckError):
        return str(exc)
    return "成绩提醒健康检查失败，请查看 GitHub Actions 日志。"


def print_config_check(settings) -> None:
    token_info = parse_token(settings.ruc_token)
    cookies = parse_cookie_header(settings.ruc_cookie)
    session = cookies.get("SESSION", "")
    password_login_configured = bool(settings.ruc_username and settings.ruc_password)

    print("本地配置体检（已隐藏敏感值）")
    print(f"- 登录方式: {'账号密码直登' if password_login_configured else '手动 TOKEN'}")
    print(f"- RUC_USERNAME: {format_present(settings.ruc_username)}")
    print(f"- RUC_PASSWORD: {format_present(settings.ruc_password, reveal=False)}")
    print(f"- RUC_TOKEN: {format_present(settings.ruc_token)}")
    if settings.ruc_token:
        print(f"  - JWT 片段数: {len(settings.ruc_token.split('.'))}")
        print(f"  - 账号: {mask_secret(token_info.account or '')}")
        print(f"  - sid: {mask_secret(token_info.session_id or '')}")
        print(f"  - 过期时间: {format_timestamp(token_info.expires_at)}")
    print(f"- RUC_COOKIE: {format_present(settings.ruc_cookie)}")
    if settings.ruc_cookie:
        names = sorted(cookies)
        print(f"  - Cookie 数量: {len(names)}")
        print(f"  - 包含 SESSION: {'是' if session else '否'}")
        print(f"  - SESSION: {mask_secret(session)}")
        print(f"  - 包含 access_token: {'是' if 'access_token' in cookies else '否'}")
        print(f"  - 包含 authcode: {'是' if 'authcode' in cookies else '否'}")
        if token_info.session_id and session:
            same_session = token_info.session_id == session
            print(f"  - TOKEN.sid 与 Cookie.SESSION 一致: {'是' if same_session else '否'}")
    print(f"- PUSHPLUS_TOKEN: {format_present(settings.pushplus_token)}")
    if settings.pushplus_token:
        print(f"  - 长度: {len(settings.pushplus_token)}")
        print(f"  - 形状像 32 位十六进制 token: {'是' if looks_like_pushplus_token(settings.pushplus_token) else '否'}")
    print(f"- GRADE_HASH_SALT: {format_present(settings.grade_hash_salt)}")


def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if name:
            cookies[name] = value.strip()
    return cookies


def format_present(value: str, reveal: bool = True) -> str:
    if not value:
        return "未配置"
    masked = mask_secret(value) if reveal else "*" * min(len(value), 8)
    return f"已配置，长度 {len(value)}，值 {masked}"


def mask_secret(value: str) -> str:
    if not value:
        return "未读取到"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def format_timestamp(timestamp: int | None) -> str:
    if timestamp is None:
        return "未读取到"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def looks_like_pushplus_token(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdefABCDEF" for char in value)


if __name__ == "__main__":
    raise SystemExit(main())
