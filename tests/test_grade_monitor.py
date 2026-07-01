from __future__ import annotations

import unittest

from check_grades import (
    HealthCheckError,
    health_failure_message,
    looks_like_pushplus_token,
    mask_secret,
    parse_cookie_header,
    safe_failure_message,
    safe_failure_title,
)
from config import Settings
from grade_diff import fingerprint_set, find_new_grades, normalize_grade_rows
from ruc_jw_client import (
    RucAuthError,
    RucJwClient,
    RucResponseError,
    cookie_value,
    qz_conditions,
)
from ruc_auth import (
    describe_login_error,
    extract_csrf_token,
    extract_login_iframe_url,
    format_vruc_username,
    raw_query_value,
)


class QzEncodingTest(unittest.TestCase):
    def test_empty_conditions_match_frontend_encoding(self) -> None:
        self.assertEqual(
            qz_conditions(),
            "QZDATASOFTJddJJVIJY29uZGl0aW9uR3JvdXAlMjIlM0ElNUIlN0IlMjJsaW5rJTIyJTNBJTIyYW5kJTIyJTJDJTIyY29uZGl0aW9uJTIyJTNBJTVCJTVEJTdEyTTECTTE",
        )


class GradeDiffTest(unittest.TestCase):
    def test_normalize_skips_summary_and_empty_score_rows(self) -> None:
        grades = normalize_grade_rows(
            [
                {"xnxq": "2025-2026-2", "zxf": 10},
                {"kcname": "数据库系统", "zcjname1": "", "jd": ""},
                {
                    "xnxq": "2025-2026-2",
                    "kcname": "数据库系统",
                    "jsname": "张老师",
                    "xf": "3",
                    "zcjname1": "92",
                    "jd": "4.0",
                    "cjbzname": "正常",
                },
            ]
        )
        self.assertEqual(len(grades), 1)
        self.assertEqual(grades[0].course_name, "数据库系统")
        self.assertEqual(grades[0].final_score, "92")

    def test_find_new_grades(self) -> None:
        grades = normalize_grade_rows(
            [
                {"kcname": "A", "zcjname1": "90"},
                {"kcname": "B", "zcjname1": "91"},
            ]
        )
        seen = {grades[0].fingerprint("salt")}
        new_grades = find_new_grades(grades, seen, "salt")
        self.assertEqual([grade.course_name for grade in new_grades], ["B"])

    def test_fingerprint_set_is_stable(self) -> None:
        grades = normalize_grade_rows([{"kcname": "A", "zcjname1": "90"}])
        self.assertEqual(fingerprint_set(grades, "salt"), fingerprint_set(grades, "salt"))


class RucResponseParsingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = RucJwClient(Settings(ruc_token="token", pushplus_token="", grade_hash_salt=""))

    def test_unwraps_common_paginated_records_shape(self) -> None:
        rows = self.client._unwrap_grade_rows(
            {"code": 0, "data": {"records": [{"kcname": "A", "zcjname1": "90"}]}}
        )
        self.assertEqual(rows, [{"kcname": "A", "zcjname1": "90"}])

    def test_auth_message_is_reported_as_auth_error(self) -> None:
        with self.assertRaises(RucAuthError):
            self.client._unwrap_grade_rows({"code": 401, "msg": "登录超时"})

    def test_wrapped_401_status_is_reported_as_auth_error(self) -> None:
        with self.assertRaises(RucAuthError):
            self.client._unwrap_grade_rows({"code": "security.httpstatu.401.1006"})

    def test_unknown_shape_reports_safe_summary(self) -> None:
        with self.assertRaisesRegex(RucResponseError, "top-level keys"):
            self.client._unwrap_grade_rows({"code": 0, "data": {"total": 1}})

    def test_cookie_value_reads_session(self) -> None:
        self.assertEqual(cookie_value("a=1; SESSION=abc; token=", "SESSION"), "abc")

    def test_token_cookie_session_mismatch_fails_before_request(self) -> None:
        client = RucJwClient(
            Settings(
                ruc_token=(
                    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9."
                    "eyJhY2MiOiIyMDI0IiwiZXhwIjo0MTAyNDQ0ODAwLCJzaWQiOiJhYmMifQ."
                    "signature"
                ),
                ruc_cookie="SESSION=def",
                pushplus_token="",
                grade_hash_salt="",
            )
        )
        with self.assertRaisesRegex(RucAuthError, "SESSION"):
            client._ensure_token_usable()


class RucPasswordLoginTest(unittest.TestCase):
    def test_formats_vruc_username_for_student_account(self) -> None:
        self.assertEqual(format_vruc_username("20240001"), "ruc:20240001")
        self.assertEqual(format_vruc_username("ruc:20240001"), "ruc:20240001")
        self.assertEqual(format_vruc_username("name@example.com"), "name@example.com")
        self.assertEqual(format_vruc_username("13800138000"), "%2B86 13800138000")

    def test_extracts_login_iframe_and_csrf_token(self) -> None:
        html = (
            '<iframe id="login-iframe" src="/auth/login?proxy=true&redirect_uri=a%2Fb">'
            "</iframe>"
            '<input type="hidden" name="csrftoken" value="abc123" id="csrftoken" />'
        )
        self.assertEqual(
            extract_login_iframe_url(html, "https://v.ruc.edu.cn/account/login"),
            "https://v.ruc.edu.cn/auth/login?proxy=true&redirect_uri=a%2Fb",
        )
        self.assertEqual(extract_csrf_token(html), "abc123")

    def test_raw_query_value_preserves_encoded_redirect_uri(self) -> None:
        url = "https://v.ruc.edu.cn/auth/login?proxy=true&redirect_uri=a%3Fb%3D1%26c%3D2"
        self.assertEqual(raw_query_value(url, "redirect_uri"), "a%3Fb%3D1%26c%3D2")

    def test_login_errors_are_actionable(self) -> None:
        self.assertIn("账号或密码", describe_login_error("verification failed"))
        self.assertIn("图片验证码", describe_login_error("captcha error"))
        self.assertIn("二次验证码", describe_login_error("need twofactor"))


class ConfigCheckTest(unittest.TestCase):
    def test_parse_cookie_header(self) -> None:
        self.assertEqual(
            parse_cookie_header("a=1; SESSION=abc; token="),
            {"a": "1", "SESSION": "abc", "token": ""},
        )

    def test_mask_secret_hides_middle(self) -> None:
        self.assertEqual(mask_secret("1234567890"), "1234...7890")

    def test_pushplus_token_shape(self) -> None:
        self.assertTrue(looks_like_pushplus_token("658716a2add245a7b3dc4346d83fb594"))
        self.assertFalse(looks_like_pushplus_token("TOKEN:658716a2add245a7b3dc4346d83fb594"))

    def test_failure_notification_avoids_sensitive_words(self) -> None:
        self.assertEqual(safe_failure_title("教务 TOKEN 已过期"), "教务登录失效")
        message = safe_failure_message("教务登录态失效")
        self.assertNotIn("TOKEN", message)
        self.assertNotIn("COOKIE", message)

    def test_health_failure_message_classifies_auth_and_tests(self) -> None:
        self.assertIn("教务登录失败", health_failure_message(RucAuthError("401")))
        self.assertIn("代码自检未通过", health_failure_message(HealthCheckError("代码自检未通过")))


if __name__ == "__main__":
    unittest.main()
