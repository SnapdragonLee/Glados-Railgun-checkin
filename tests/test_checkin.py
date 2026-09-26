from types import SimpleNamespace

import pytest

import checkin


ENV_NAMES = (
    "GLADOS_COOKIES",
    "GLADOS_EXCHANGE_PLAN",
    "GLADOS_VERBOSE",
    "PUSHDEER_SENDKEY",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_config_defaults_to_single_domain_and_exchange_off(monkeypatch):
    monkeypatch.setenv("GLADOS_COOKIES", "cookie-one & cookie-two")

    config = checkin.Config()

    assert config.cookies_list == ["cookie-one", "cookie-two"]
    assert config.DOMAINS == ("glados.one",)
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
        checkin.API("railgun.info")


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

    api.session = FakeSession()
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
        DOMAINS=("glados.one",),
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
