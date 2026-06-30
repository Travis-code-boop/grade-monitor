from __future__ import annotations

import argparse
from datetime import datetime
import sys

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor RUC JW grade updates.")
    parser.add_argument("--dry-run", action="store_true", help="Do not notify or save state.")
    parser.add_argument("--notify-test", action="store_true", help="Send a PushPlus test message.")
    parser.add_argument("--baseline-notify", action="store_true", help="Notify on first baseline run.")
    parser.add_argument("--config-check", action="store_true", help="Print a redacted local config check.")
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
        return "教务登录态失效，请重新登录教务系统，并更新同一次成绩请求里的教务凭据。"
    if "接口" in title:
        return "教务成绩接口返回异常，请查看 GitHub Actions 日志。"
    if "网络" in title:
        return "教务网络请求失败，请查看 GitHub Actions 日志。"
    return "成绩监控运行失败，请查看 GitHub Actions 日志。"


def print_config_check(settings) -> None:
    token_info = parse_token(settings.ruc_token)
    cookies = parse_cookie_header(settings.ruc_cookie)
    session = cookies.get("SESSION", "")

    print("本地配置体检（已隐藏敏感值）")
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


def format_present(value: str) -> str:
    if not value:
        return "未配置"
    return f"已配置，长度 {len(value)}，值 {mask_secret(value)}"


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
