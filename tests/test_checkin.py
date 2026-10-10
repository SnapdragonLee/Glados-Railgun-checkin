from types import SimpleNamespace

import pytest
from urllib3.exceptions import ConnectTimeoutError, MaxRetryError

import checkin


ENV_NAMES = (
    "GLADOS_COOKIES",
    "GLADOS_EXCHANGE_PLAN",
    "GLADOS_VERBOSE",
    "PUSHDEER_SENDKEY",
    "GLADOS_DOMAINS",
    "GITHUB_ACTIONS",
    "GITHUB_STEP_SUMMARY",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_config_defaults_to_single_domain_and_exchange_off(monkeypatch):
    monkeypatch.setenv("GLADOS_COOKIES", "cookie-one & cookie-two")

    config = checkin.Config()

    assert config.cookies_list == ["cookie-one", "cookie-two"]
    assert config.domains == ("glados.one",)
    assert config.DOMAINS == ("glados.one", "glados.cloud", "railgun.info")
    assert config.exchange_plan == "off"
    assert config.verbose is False


@pytest.mark.parametrize("value", ["", "unexpected", "plan999"])
def test_config_rejects_invalid_exchange_plan(monkeypatch, value):
    monkeypatch.setenv("GLADOS_COOKIES", "cookie")
    monkeypatch.setenv("GLADOS_EXCHANGE_PLAN", value)

    if value == "":
        assert checkin.Config().exchange_plan == "off"
    else:
        with pytest.raises(ValueError, match="GLADOS_EXCHANGE_PLAN"):
            checkin.Config()


def test_config_requires_cookie():
    with pytest.raises(ValueError, match="GLADOS_COOKIES"):
        checkin.Config()


def test_api_rejects_unapproved_domain():
    with pytest.raises(ValueError, match="未允许"):
        checkin.API("untrusted.example")


def test_request_refuses_redirect_and_does_not_log_response_body(caplog):
    api = checkin.API("glados.one")
    secret_body = "sensitive-account-response"
    response = SimpleNamespace(status_code=302, ok=False, text=secret_body)
    captured = {}

    class FakeSession:
        def request(self, method, url, **kwargs):
            captured.update(method=method, url=url, **kwargs)
            return response

        def close(self):
            pass

    api.query_session = FakeSession()
    assert api._make_request("/api/user/status", "GET", cookies="secret-cookie") is None
    assert captured["url"] == "https://glados.one/api/user/status"
    assert captured["allow_redirects"] is False
    assert captured["headers"]["cookie"] == "secret-cookie"
    assert secret_body not in caplog.text


def test_checkin_matches_successful_browser_request():
    api = checkin.API("glados.one")
    captured = {}

    class FakeSession:
        def request(self, method, url, **kwargs):
            captured.update(method=method, url=url, **kwargs)
            return SimpleNamespace(status_code=200, ok=True)

        def close(self):
            pass

    api.session = FakeSession()
    api._make_request("/api/user/checkin", "POST", {"token": "glados.one"}, "cookie")

    assert api.headers["user-agent"].startswith("Mozilla/5.0 (Macintosh;")
    assert api.headers["accept"] == "application/json, text/plain, */*"
    assert captured["data"] == '{"token":"glados.one"}'
    assert captured["headers"]["content-type"] == "application/json;charset=UTF-8"
    assert "json" not in captured
    assert captured["allow_redirects"] is False


def test_exchange_keeps_form_data():
    api = checkin.API("glados.one")
    captured = {}

    class FakeSession:
        def request(self, method, url, **kwargs):
            captured.update(method=method, url=url, **kwargs)
            return SimpleNamespace(status_code=200, ok=True)

        def close(self):
            pass

    api.session = FakeSession()
    api._make_request("/api/user/exchange", "POST", {"planType": "plan500"}, "cookie")

    assert captured["data"] == {"planType": "plan500"}
    assert "content-type" not in captured["headers"]


class FakeAPI:
    status = ("365 天", 0)
    checkin_result = {
        "status": "签到成功",
        "points": "10",
        "message": "ok",
        "code": checkin.CheckinStatus.SUCCESS,
    }
    points = ("499 积分", 499)
    exchange_result = ("兑换成功: plan500", True)
    calls = []

    def __init__(self, domain, cookie_index=0, verbose=False):
        self.domain = domain

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_status(self, cookie):
        self.calls.append("status")
        return self.status

    def checkin(self, cookie):
        self.calls.append("checkin")
        return self.checkin_result

    def get_points(self, cookie):
        self.calls.append("points")
        return self.points

    def exchange(self, cookie, plan):
        self.calls.append(("exchange", plan))
        return self.exchange_result


def make_config(exchange_plan="off"):
    return SimpleNamespace(
        cookies_list=["cookie"],
        domains=("glados.one",),
        exchange_plan=exchange_plan,
        EXCHANGE_PLANS={"plan100": 100, "plan200": 200, "plan500": 500},
        verbose=False,
    )


def reset_fake_api():
    FakeAPI.calls = []
    FakeAPI.status = ("365 天", 0)
    FakeAPI.checkin_result = {
        "status": "签到成功",
        "points": "10",
        "message": "ok",
        "code": checkin.CheckinStatus.SUCCESS,
    }
    FakeAPI.points = ("499 积分", 499)
    FakeAPI.exchange_result = ("兑换成功: plan500", True)


def test_exchange_is_skipped_below_threshold(monkeypatch):
    reset_fake_api()
    monkeypatch.setattr(checkin, "API", FakeAPI)
    checker = checkin.Checker(make_config("plan500"))

    checker.checkin_all()

    assert FakeAPI.calls == ["status", "checkin", "points"]
    assert checker.results[0].exchange == "未兑换: 499/500 积分"
    assert checker.has_failures() is False


def test_exchange_runs_at_threshold(monkeypatch):
    reset_fake_api()
    FakeAPI.points = ("500 积分", 500)
    monkeypatch.setattr(checkin, "API", FakeAPI)
    checker = checkin.Checker(make_config("plan500"))

    checker.checkin_all()

    assert FakeAPI.calls == ["status", "checkin", "points", ("exchange", "plan500")]
    assert checker.results[0].exchange == "兑换成功: plan500"
    assert checker.has_failures() is False


def test_failure_stops_before_points_and_exchange(monkeypatch):
    reset_fake_api()
    FakeAPI.checkin_result = {
        "status": "签到失败",
        "points": "0",
        "message": "unauthorized",
        "code": checkin.CheckinStatus.FAILURE,
    }
    monkeypatch.setattr(checkin, "API", FakeAPI)
    checker = checkin.Checker(make_config("plan500"))

    checker.checkin_all()

    assert FakeAPI.calls == ["status", "checkin"]
    assert checker.results[0].exchange == "未兑换: 签到失败"
    assert checker.has_failures() is True


def test_repeat_checkin_is_successful_for_exit_status(monkeypatch):
    reset_fake_api()
    FakeAPI.checkin_result = {
        "status": "今日已签到",
        "points": "0",
        "message": "repeat",
        "code": checkin.CheckinStatus.REPEAT,
    }
    monkeypatch.setattr(checkin, "API", FakeAPI)
    checker = checkin.Checker(make_config())

    checker.checkin_all()

    assert checker.has_failures() is False
    assert "今日已签到" in checker.format_results()[0]


def test_repeat_checkin_never_triggers_exchange(monkeypatch):
    reset_fake_api()
    FakeAPI.checkin_result = {
        "status": "今日已签到",
        "points": "0",
        "message": "repeat",
        "code": checkin.CheckinStatus.REPEAT,
    }
    FakeAPI.points = ("500 积分", 500)
    monkeypatch.setattr(checkin, "API", FakeAPI)
    checker = checkin.Checker(make_config("plan500"))

    checker.checkin_all()

    assert FakeAPI.calls == ["status", "checkin", "points"]
    assert checker.results[0].exchange == "未兑换: 本次为重复签到"
    assert checker.has_failures() is False


def test_api_accepts_string_codes(monkeypatch):
    class FakeResponse:
        ok = True
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    api = checkin.API("glados.one")
    monkeypatch.setattr(
        api,
        "_make_request",
        lambda *args, **kwargs: FakeResponse({"code": "0", "points": 10, "message": "ok"}),
    )
    assert api.checkin("cookie")["code"] == checkin.CheckinStatus.SUCCESS

    monkeypatch.setattr(
        api,
        "_make_request",
        lambda *args, **kwargs: FakeResponse({"code": "0", "message": "ok"}),
    )
    assert api.exchange("cookie", "plan500") == ("兑换成功: plan500", True)


def test_main_returns_nonzero_without_cookie(monkeypatch):
    monkeypatch.setattr(checkin.PushService, "send", lambda *args, **kwargs: False)
    assert checkin.main() == 1


@pytest.mark.parametrize("domain", checkin.Config.DOMAINS)
def test_api_accepts_supported_domains(domain):
    with checkin.API(domain) as api:
        assert api._get_full_url("/api/user/status") == f"https://{domain}/api/user/status"
        retry = api.query_session.adapters["https://"].max_retries
        assert retry.allowed_methods == frozenset({"GET"})


def test_config_selects_domains_and_removes_duplicates(monkeypatch):
    monkeypatch.setenv("GLADOS_COOKIES", "cookie")
    monkeypatch.setenv("GLADOS_DOMAINS", "glados.cloud, GLADOS.ONE,glados.cloud,railgun.info")
    assert checkin.Config().domains == ("glados.cloud", "glados.one", "railgun.info")


@pytest.mark.parametrize("domains", ["glados.one,", "https://glados.one", "evil.example", "glados.one:443"])
def test_config_rejects_untrusted_domain_input(monkeypatch, domains):
    monkeypatch.setenv("GLADOS_COOKIES", "cookie")
    monkeypatch.setenv("GLADOS_DOMAINS", domains)
    with pytest.raises(ValueError, match="GLADOS_DOMAINS"):
        checkin.Config()


def response(payload):
    return SimpleNamespace(status_code=200, ok=True, json=lambda: payload)


def test_device_mismatch_retries_once_and_reuses_matched_platform(monkeypatch):
    api = checkin.API("glados.one")
    responses = iter([
        {"code": "4", "reason": "device-mismatch", "loginDevice": "Windows", "message": "Automated check-in detected"},
        {"code": "1", "message": "already checked in"},
    ])
    uas = []

    def request(*args, **kwargs):
        uas.append(kwargs["user_agent"])
        return response(next(responses))

    monkeypatch.setattr(api, "_make_request", request)
    result = api.checkin("cookie")
    assert uas == [checkin.PLATFORM_UA["macOS"], checkin.PLATFORM_UA["Windows"]]
    assert result["code"] == checkin.CheckinStatus.REPEAT
    assert result["api_code"] == 1
    assert result["attempts"] == 2
    assert api.session.headers["user-agent"] == checkin.PLATFORM_UA["Windows"]
    assert api.query_session.headers["user-agent"] == checkin.PLATFORM_UA["Windows"]


@pytest.mark.parametrize("payload", [
    {"code": 4, "loginDevice": "Windows"},
    {"code": 4, "reason": "other-reason", "loginDevice": "Windows"},
    {"code": 4, "reason": "device-mismatch", "loginDevice": "unknown-device"},
    {"code": 4, "reason": "device-mismatch", "loginDevice": "macOS"},
    {"code": -2, "reason": "device-mismatch", "loginDevice": "Windows"},
])
def test_other_rejections_are_not_retried(monkeypatch, payload):
    api = checkin.API("glados.one")
    calls = []
    monkeypatch.setattr(api, "_make_request", lambda *args, **kwargs: calls.append(kwargs) or response(payload))
    result = api.checkin("cookie")
    assert len(calls) == 1
    assert result["code"] == checkin.CheckinStatus.FAILURE
    assert result["api_code"] == payload["code"]


def test_second_device_rejection_preserves_reason_and_stops(monkeypatch):
    api = checkin.API("glados.one")
    calls = []
    payload = {"code": 4, "reason": "device-mismatch", "loginDevice": "Windows", "message": "Please sign in again"}
    monkeypatch.setattr(api, "_make_request", lambda *args, **kwargs: calls.append(kwargs) or response(payload))
    result = api.checkin("cookie")
    assert len(calls) == 2
    assert result["attempts"] == 2
    assert "reason=device-mismatch" in result["message"]
    assert "loginDevice=Windows" in result["message"]
    assert result["code"] == checkin.CheckinStatus.FAILURE


def test_authentication_failure_skips_signin_and_exchange(monkeypatch):
    reset_fake_api()
    FakeAPI.status = ("None 天", -2)
    monkeypatch.setattr(checkin, "API", FakeAPI)
    checker = checkin.Checker(make_config("plan500"))
    checker.checkin_all()
    assert FakeAPI.calls == ["status"]
    assert checker.has_failures() is True
    assert checker.results[0].api_code == -2
    assert "状态查询失败" in checker.results[0].diagnostic


def test_status_retains_authenticated_business_error(monkeypatch):
    api = checkin.API("glados.one")
    monkeypatch.setattr(api, "_make_request", lambda *args, **kwargs: response({"code": -2, "message": "没有权限"}))
    assert api.get_status("cookie")[1] == -2
    assert api.last_api_code == -2
    assert "认证失败" in api.last_error


def test_exchange_is_attempted_once_per_cookie_across_domains(monkeypatch):
    reset_fake_api()
    FakeAPI.points = ("1000 积分", 1000)
    monkeypatch.setattr(checkin, "API", FakeAPI)
    config = make_config("plan500")
    config.domains = ("glados.one", "glados.cloud")
    checker = checkin.Checker(config)
    checker.checkin_all()
    assert FakeAPI.calls.count(("exchange", "plan500")) == 1
    assert checker.results[1].exchange == "未兑换: 本轮该 Cookie 已尝试兑换"


def test_diagnostics_redact_session_values_and_account_data(monkeypatch, caplog):
    secret = "session-private-value"
    cookie = f"gld:sess={secret}; gld:sess.sig=private-signature"
    api = checkin.API("glados.one")
    payload = {"code": 4, "reason": "device-mismatch", "loginDevice": "macOS", "message": f"{secret} private-signature account@example.invalid https://example.invalid/?token=abc\n::error::injected"}
    monkeypatch.setattr(api, "_make_request", lambda *args, **kwargs: response(payload))
    result = api.checkin(cookie)
    for sensitive in (secret, "private-signature", "account@example.invalid", "token=abc"):
        assert sensitive not in result["message"]
        assert sensitive not in caplog.text
    assert "\n" not in result["message"]
    assert "code=4" in result["message"]


def test_invalid_payload_becomes_sanitized_failure(monkeypatch):
    api = checkin.API("glados.one")
    monkeypatch.setattr(api, "_make_request", lambda *args, **kwargs: response(["private-response"]))
    result = api.checkin("cookie")
    assert result["code"] == checkin.CheckinStatus.FAILURE
    assert result["message"] == "响应解析失败"


def test_actions_summary_and_annotation_show_failure_reason(monkeypatch, tmp_path, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    checker = checkin.Checker(make_config())
    checker.results = [checkin.CheckinResult(1, "glados.one", api_code=4, diagnostic="code=4；reason=device-mismatch | 100%", attempts=2)]
    checker.report_actions()
    assert "reason=device-mismatch" in summary.read_text()
    assert "\\|" in summary.read_text()
    output = capsys.readouterr().out
    assert "::error title=GLaDOS 签到失败::" in output
    assert "100%25" in output


def test_no_results_is_failure():
    assert checkin.Checker(make_config()).has_failures() is True


def test_points_query_reports_business_rejection(monkeypatch):
    api = checkin.API("glados.one")
    monkeypatch.setattr(api, "_make_request", lambda *args, **kwargs: response({"code": -2, "message": "没有权限"}))
    assert api.get_points("cookie") == ("None 积分", None)
    assert "code=-2" in api.last_error


def test_main_reports_all_domain_failures_and_returns_nonzero(monkeypatch, tmp_path, capsys):
    reset_fake_api()
    FakeAPI.status = ("None 天", -2)
    monkeypatch.setattr(checkin, "API", FakeAPI)
    monkeypatch.setattr(checkin.PushService, "send", lambda *args, **kwargs: False)
    monkeypatch.setenv("GLADOS_COOKIES", "fake-cookie")
    monkeypatch.setenv("GLADOS_DOMAINS", ",".join(checkin.Config.DOMAINS))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert checkin.main() == 1
    assert FakeAPI.calls == ["status", "status", "status"]
    assert capsys.readouterr().out.count("::error title=") == 3
    assert all(domain in summary.read_text() for domain in checkin.Config.DOMAINS)


@pytest.mark.parametrize("path", ["/api/user/checkin", "/api/user/exchange"])
def test_post_transport_never_retries_connection_errors(monkeypatch, path):
    with checkin.API("glados.one") as api:
        calls = []

        def post_request(method, url, **kwargs):
            calls.append((method, url))
            retry = api.session.get_adapter(url).max_retries
            with pytest.raises(MaxRetryError):
                retry.increment(method=method, url=url, error=ConnectTimeoutError(None, "synthetic"))
            return response({"code": 0})

        monkeypatch.setattr(api.session, "request", post_request)
        monkeypatch.setattr(api.query_session, "request", lambda *args, **kwargs: pytest.fail("POST used GET retry session"))
        api._make_request(path, "POST", {}, "cookie")
        assert len(calls) == 1
        get_retry = api.query_session.adapters["https://"].max_retries
        assert get_retry.increment(method="GET", error=ConnectTimeoutError(None, "synthetic")).connect == 1


@pytest.mark.parametrize("secret", ["a", "ab", "abc"])
def test_short_cookie_values_are_redacted(secret):
    assert secret not in checkin.safe_text(f"echoed {secret}", f"gld:sess={secret}")


@pytest.mark.parametrize("payload", [{"code": 0}, {"code": 0, "data": {}}])
def test_incomplete_status_stops_before_any_post(monkeypatch, payload):
    calls = []

    def request(self, path, method, **kwargs):
        calls.append((path, method))
        return response(payload)

    monkeypatch.setattr(checkin.API, "_make_request", request)
    checker = checkin.Checker(make_config("plan500"))
    checker.checkin_all()
    assert calls == [("/api/user/status", "GET")]
    assert checker.has_failures()
    assert "leftDays" in checker.results[0].diagnostic


@pytest.mark.parametrize("plan,status", [("off", checkin.CheckinStatus.SUCCESS), ("plan500", checkin.CheckinStatus.REPEAT)])
def test_points_failure_is_reported_even_without_exchange(monkeypatch, tmp_path, capsys, plan, status):
    reset_fake_api()
    FakeAPI.points = ("None 积分", None)
    FakeAPI.checkin_result = dict(FakeAPI.checkin_result, code=status)
    monkeypatch.setattr(checkin, "API", FakeAPI)
    monkeypatch.setattr(checkin.PushService, "send", lambda *args, **kwargs: False)
    monkeypatch.setenv("GLADOS_COOKIES", "fake-cookie")
    monkeypatch.setenv("GLADOS_EXCHANGE_PLAN", plan)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert checkin.main() == 1
    assert FakeAPI.calls == ["status", "checkin", "points"]
    assert "积分查询失败" in summary.read_text()
    assert "积分查询失败" in capsys.readouterr().out


def test_duplicate_cookies_cannot_trigger_a_second_exchange(monkeypatch):
    reset_fake_api()
    FakeAPI.points = ("1000 积分", 1000)
    monkeypatch.setattr(checkin, "API", FakeAPI)
    monkeypatch.setenv("GLADOS_COOKIES", "same-cookie & same-cookie")
    monkeypatch.setenv("GLADOS_DOMAINS", "glados.one,glados.cloud")
    monkeypatch.setenv("GLADOS_EXCHANGE_PLAN", "plan500")
    config = checkin.Config()
    assert config.cookies_list == ["same-cookie"]
    checker = checkin.Checker(config)
    checker.checkin_all()
    assert FakeAPI.calls.count(("exchange", "plan500")) == 1


def test_configuration_error_has_actions_annotation(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(checkin.PushService, "send", lambda *args, **kwargs: False)
    assert checkin.main() == 1
    assert "::error title=GLaDOS 运行失败::" in capsys.readouterr().out
