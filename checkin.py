import json
import os
import re
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import requests
from pypushdeer import PushDeer
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from logging_config import init_logger


class CheckinStatus(Enum):
    """签到状态。"""

    SUCCESS = 0
    REPEAT = 1
    FAILURE = -2


class ExchangePlan(Enum):
    """兑换计划。"""

    OFF = "off"
    PLAN100 = "plan100"
    PLAN200 = "plan200"
    PLAN500 = "plan500"


class APIEndpoint(Enum):
    """GLaDOS API 端点。"""

    CHECKIN = "/api/user/checkin"
    STATUS = "/api/user/status"
    POINTS = "/api/user/points"
    EXCHANGE = "/api/user/exchange"


class LogEmoji:
    SUCCESS = "✅"
    FAIL = "❌"
    REPEAT = "🔄"
    CHECKIN = "🎫"
    STATUS = "📊"
    POINTS = "💰"
    EXCHANGE = "🎁"
    START = "🚀"
    END = "🏁"
    COOKIE = "🍪"
    DOMAIN = "🌐"
    WARNING = "⚠️"
    ERROR = "🔴"
    INFO = "ℹ️"


PLATFORM_UA = {
    "Windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "macOS": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
    "Linux": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "iPhone": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Android": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
}


def safe_text(value: object, cookies: str = "", secrets: Tuple[str, ...] = ()) -> str:
    """只保留有限的诊断文本，屏蔽会话、邮箱、URL 和控制字符。"""
    text = str(value)
    sensitive = [cookies, *secrets]
    sensitive.extend(part.partition("=")[2].strip() for part in re.split(r"[;&]", cookies))
    for secret in sorted((item for item in sensitive if item), key=len, reverse=True):
        text = text.replace(secret, "[已隐藏]")
    text = re.sub(r"https?://\S+", "[URL已隐藏]", text, flags=re.IGNORECASE)
    text = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[邮箱已隐藏]", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?i)\b(?:[a-z]+:sess(?:\.sig)?|cookie|token|authorization|sendkey|pushkey)\s*[:=]\s*[^\s;,]+",
        "[凭据已隐藏]",
        text,
    )
    text = re.sub(r"[A-Za-z0-9_+/=-]{32,}", "[凭据已隐藏]", text)
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return text.strip()[:240]


def api_diagnostic(payload: Dict, cookies: str = "") -> str:
    code = int(payload.get("code", -2))
    reason = safe_text(payload.get("reason", ""), cookies)
    device = payload.get("loginDevice")
    message = safe_text(payload.get("message", "无消息字段"), cookies)
    if code == -2 and any(term in message.lower() for term in ("没有权限", "no permission", "unauthorized", "未登录")):
        hint = "认证失败，请在该域名重新登录并更新对应 Cookie"
    elif code == 4 and payload.get("reason") == "device-mismatch":
        hint = "登录设备与请求平台不匹配"
    elif code == 4:
        hint = "服务端拒绝自动签到，请重新登录并核对返回原因"
    else:
        hint = "服务端业务请求失败"
    parts = [hint, f"code={code}"]
    if reason:
        parts.append(f"reason={reason}")
    if isinstance(device, str) and device in PLATFORM_UA:
        parts.append(f"loginDevice={device}")
    if message:
        parts.append(message)
    return "；".join(parts)


def log_method(func):
    """把单个 API 的解析异常转换成可汇总的失败结果。"""

    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except (
            AttributeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            requests.exceptions.JSONDecodeError,
        ) as exc:
            logger.error(
                "%s[%s] %s[%s] %s %s 响应解析失败: %s",
                LogEmoji.COOKIE,
                self.cookie_index,
                LogEmoji.DOMAIN,
                self.domain,
                LogEmoji.ERROR,
                func.__name__,
                type(exc).__name__,
            )
            self.last_error = f"响应解析失败: {type(exc).__name__}"
            defaults = {
                "checkin": {
                    "status": "签到失败",
                    "points": "0",
                    "message": "响应解析失败",
                    "code": CheckinStatus.FAILURE,
                },
                "get_status": ("None 天", -2),
                "get_points": ("None 积分", None),
                "exchange": ("兑换失败: 响应解析失败", False),
            }
            return defaults[func.__name__]

    return wrapper


class Config:
    """从环境变量加载运行配置。"""

    ENV_PUSH_KEY = "PUSHDEER_SENDKEY"
    ENV_COOKIES = "GLADOS_COOKIES"
    ENV_EXCHANGE_PLAN = "GLADOS_EXCHANGE_PLAN"
    ENV_VERBOSE = "GLADOS_VERBOSE"
    ENV_DOMAINS = "GLADOS_DOMAINS"

    DEFAULT_EXCHANGE_PLAN = ExchangePlan.OFF.value
    DEFAULT_VERBOSE = False

    # 支持列表限定 Cookie 的接收方；运行列表由已验证的域名配置决定。
    DOMAINS = ("glados.one", "glados.cloud", "railgun.info")
    DEFAULT_DOMAINS = ("glados.one",)

    EXCHANGE_PLANS = {
        ExchangePlan.PLAN100.value: 100,
        ExchangePlan.PLAN200.value: 200,
        ExchangePlan.PLAN500.value: 500,
    }

    def __init__(self):
        self.push_key = ""
        self.cookies_list: List[str] = []
        self.exchange_plan = self.DEFAULT_EXCHANGE_PLAN
        self.verbose = self.DEFAULT_VERBOSE
        self.domains = self.DEFAULT_DOMAINS
        self._load_config()

    def _load_config(self) -> None:
        self.push_key = os.environ.get(self.ENV_PUSH_KEY, "").strip()

        raw_cookies = os.environ.get(self.ENV_COOKIES, "")
        self.cookies_list = list(dict.fromkeys(cookie.strip() for cookie in raw_cookies.split("&") if cookie.strip()))
        if not self.cookies_list:
            raise ValueError(f"环境变量 '{self.ENV_COOKIES}' 未包含有效 Cookie。")

        raw_domains = os.environ.get(self.ENV_DOMAINS, "").strip()
        if raw_domains:
            domains = tuple(dict.fromkeys(item.strip().lower() for item in raw_domains.split(",")))
            if any(domain not in self.DOMAINS for domain in domains):
                raise ValueError(f"环境变量 '{self.ENV_DOMAINS}' 只能包含允许的域名，使用逗号分隔。")
            self.domains = domains

        exchange_plan = (
            os.environ.get(self.ENV_EXCHANGE_PLAN, "").strip().lower()
            or self.DEFAULT_EXCHANGE_PLAN
        )
        valid_plans = {ExchangePlan.OFF.value, *self.EXCHANGE_PLANS}
        if exchange_plan not in valid_plans:
            raise ValueError(
                f"环境变量 '{self.ENV_EXCHANGE_PLAN}' 的值无效；"
                f"可选值为 {', '.join(sorted(valid_plans))}。"
            )
        self.exchange_plan = exchange_plan

        verbose = os.environ.get(self.ENV_VERBOSE)
        if verbose is not None:
            verbose = verbose.strip().lower()
            if verbose in {"true", "1", "yes", "y"}:
                self.verbose = True
            elif verbose in {"false", "0", "no", "n", ""}:
                self.verbose = False
            else:
                raise ValueError(f"环境变量 '{self.ENV_VERBOSE}' 必须是布尔值。")

        logger.info("%s 共加载 %s 个 Cookie。", LogEmoji.INFO, len(self.cookies_list))
        logger.info("%s 自动兑换策略: %s。", LogEmoji.INFO, self.exchange_plan)
        logger.info("%s 详细日志: %s。", LogEmoji.INFO, self.verbose)
        logger.info("%s 签到域名: %s。", LogEmoji.INFO, ", ".join(self.domains))


class API:
    """只面向允许域名的 GLaDOS API 客户端。"""

    CONNECT_TIMEOUT = 10
    READ_TIMEOUT = 30

    def __init__(self, domain: str, cookie_index: int = 0, verbose: bool = False):
        if domain not in Config.DOMAINS:
            raise ValueError(f"拒绝访问未允许的域名: {domain}")

        self.domain = domain
        self.cookie_index = cookie_index
        self.verbose = verbose
        self.last_error = ""
        self.last_api_code: Optional[int] = None
        self.headers = {
            "origin": f"https://{domain}",
            "accept": "application/json, text/plain, */*",
            "user-agent": PLATFORM_UA["macOS"],
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.mount("https://", HTTPAdapter(max_retries=0))
        self.query_session = requests.Session()
        self.query_session.headers.update(self.headers)

        # GET 查询可安全重试；签到和兑换 POST 不自动重试，避免重复副作用。
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            status=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        self.query_session.mount("https://", HTTPAdapter(max_retries=retry))

    def close(self) -> None:
        self.session.close()
        self.query_session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _log(self, level: str, emoji: str, message: str, force: bool = False) -> None:
        if not (force or self.verbose):
            return
        log_message = (
            f"{LogEmoji.COOKIE}[{self.cookie_index}] "
            f"{LogEmoji.DOMAIN}[{self.domain}] {emoji} {message}"
        )
        getattr(logger, level)(log_message)

    def _get_full_url(self, path: str) -> str:
        return f"https://{self.domain}{path}"

    def _make_request(
        self,
        path: str,
        method: str,
        data: Optional[Dict[str, str]] = None,
        cookies: str = "",
        user_agent: Optional[str] = None,
    ) -> Optional[requests.Response]:
        self.last_error = ""
        self.last_api_code = None
        url = self._get_full_url(path)
        request_headers = {"cookie": cookies}
        if user_agent:
            request_headers["user-agent"] = user_agent
        request_data = data
        if method.upper() == "POST" and path == APIEndpoint.CHECKIN.value:
            request_headers["content-type"] = "application/json;charset=UTF-8"
            request_data = json.dumps(data, separators=(",", ":"))

        try:
            session = self.query_session if method.upper() == "GET" else self.session
            response = session.request(
                method.upper(),
                url,
                headers=request_headers,
                data=request_data,
                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
                allow_redirects=False,
            )
        except requests.exceptions.RequestException as exc:
            self.last_error = f"网络请求失败: {type(exc).__name__}"
            self._log("error", LogEmoji.ERROR, self.last_error, force=True)
            return None

        if 300 <= response.status_code < 400:
            self.last_error = "请求被重定向，已拒绝向其他地址发送 Cookie"
            self._log("error", LogEmoji.ERROR, self.last_error, force=True)
            return None
        if not response.ok:
            # 不打印 response.text，避免服务端错误页中的账户信息进入 Actions 日志。
            self.last_error = f"HTTP {response.status_code}"
            self._log("error", LogEmoji.ERROR, self.last_error, force=True)
            return None
        return response

    def _checkin_attempt(self, cookies: str, user_agent: str) -> Optional[Dict]:
        response = self._make_request(
            APIEndpoint.CHECKIN.value,
            "POST",
            {"token": self.domain},
            cookies,
            user_agent=user_agent,
        )
        if response is None:
            return None
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("签到响应不是 JSON 对象")
        return payload

    @log_method
    def checkin(self, cookies: str) -> Dict[str, Union[str, int, CheckinStatus, None]]:
        user_agent = self.headers["user-agent"]
        data = self._checkin_attempt(cookies, user_agent)
        attempts = 1
        if data is not None and int(data.get("code", -2)) == 4 and data.get("reason") == "device-mismatch":
            login_device = data.get("loginDevice")
            recovery_ua = PLATFORM_UA.get(login_device) if isinstance(login_device, str) else None
            if recovery_ua and recovery_ua != user_agent:
                self._log("warning", LogEmoji.WARNING, f"登录设备为 {login_device}，匹配平台后重试一次", force=True)
                data = self._checkin_attempt(cookies, recovery_ua)
                attempts = 2
                if data is not None and int(data.get("code", -2)) in (0, 1):
                    self.headers["user-agent"] = recovery_ua
                    self.session.headers.update({"user-agent": recovery_ua})
                    self.query_session.headers.update({"user-agent": recovery_ua})

        if data is None:
            return {
                "status": "签到失败",
                "points": "0",
                "message": self.last_error or "网络或 HTTP 请求失败",
                "code": CheckinStatus.FAILURE,
                "api_code": None,
                "attempts": attempts,
            }

        code = int(data.get("code", CheckinStatus.FAILURE.value))
        message = safe_text(data.get("message", "无消息字段"), cookies)
        points = safe_text(data.get("points", 0), cookies)
        self.last_api_code = code
        diagnostic = {
            "api_code": code,
            "attempts": attempts,
            "reason": safe_text(data.get("reason", ""), cookies),
            "login_device": data.get("loginDevice") if isinstance(data.get("loginDevice"), str) and data.get("loginDevice") in PLATFORM_UA else "",
        }

        if code == CheckinStatus.SUCCESS.value:
            return {
                "status": "签到成功",
                "points": points,
                "message": message,
                "code": CheckinStatus.SUCCESS,
                **diagnostic,
            }
        if code == CheckinStatus.REPEAT.value:
            return {
                "status": "今日已签到",
                "points": "0",
                "message": message,
                "code": CheckinStatus.REPEAT,
                **diagnostic,
            }
        self.last_error = api_diagnostic(data, cookies)
        self._log("error", LogEmoji.ERROR, self.last_error, force=True)
        return {
            "status": "签到失败",
            "points": "0",
            "message": self.last_error,
            "code": CheckinStatus.FAILURE,
            **diagnostic,
        }

    @log_method
    def get_status(self, cookies: str) -> Tuple[str, int]:
        response = self._make_request(APIEndpoint.STATUS.value, "GET", cookies=cookies)
        if response is None:
            return "None 天", -2

        data = response.json()
        code = int(data.get("code", -2))
        self.last_api_code = code
        status_data = data.get("data")
        left_days = status_data.get("leftDays") if isinstance(status_data, dict) else None
        if code != 0:
            self.last_error = api_diagnostic(data, cookies)
            self._log("error", LogEmoji.ERROR, self.last_error, force=True)
            return "None 天", code
        if left_days is None:
            self.last_error = "状态响应缺少 data.leftDays 字段，未确认登录状态"
            return "None 天", -2
        return f"{int(float(left_days))} 天", code

    @log_method
    def get_points(self, cookies: str) -> Tuple[str, Optional[int]]:
        response = self._make_request(APIEndpoint.POINTS.value, "GET", cookies=cookies)
        if response is None:
            return "None 积分", None

        data = response.json()
        self.last_api_code = int(data["code"]) if "code" in data else None
        if "code" in data and int(data["code"]) != 0:
            self.last_error = api_diagnostic(data, cookies)
            self._log("error", LogEmoji.ERROR, self.last_error, force=True)
            return "None 积分", None
        points = data.get("points")
        if points is None:
            self.last_error = "积分响应缺少 points 字段"
            return "None 积分", None
        points_number = int(float(points))
        return f"{points_number} 积分", points_number

    @log_method
    def exchange(self, cookies: str, plan: str) -> Tuple[str, bool]:
        response = self._make_request(
            APIEndpoint.EXCHANGE.value,
            "POST",
            {"planType": plan},
            cookies,
        )
        if response is None:
            return "兑换失败: 网络或 HTTP 请求失败", False

        data = response.json()
        if int(data.get("code", -2)) == 0:
            return f"兑换成功: {plan}", True
        self.last_error = api_diagnostic(data, cookies)
        return f"兑换失败: {self.last_error}", False


@dataclass
class CheckinResult:
    cookie_index: int
    domain: str
    status: str = "签到失败"
    points: str = "0"
    days: str = "None 天"
    points_total: str = "None 积分"
    exchange: str = "自动兑换已关闭"
    code: CheckinStatus = CheckinStatus.FAILURE
    exchange_failed: bool = False
    exchange_attempted: bool = False
    query_failed: bool = False
    api_code: Optional[int] = None
    diagnostic: str = ""
    attempts: int = 0

    def to_dict(self) -> Dict[str, Union[str, bool, CheckinStatus]]:
        return asdict(self)


class PushService:
    def __init__(self, push_key: str = ""):
        self.push_key = push_key

    def send(self, title: str, content: str) -> bool:
        if not self.push_key:
            logger.info("%s 未设置 PushDeer 密钥，跳过通知。", LogEmoji.INFO)
            return False
        try:
            sent = PushDeer(pushkey=self.push_key).send_text(title, desp=content)
            if sent:
                logger.info("%s PushDeer 通知发送成功。", LogEmoji.SUCCESS)
                return True
            logger.error("%s PushDeer 服务未确认通知成功。", LogEmoji.ERROR)
            return False
        except Exception as exc:  # 推送失败不覆盖签到的真实结果。
            logger.error("%s PushDeer 通知失败: %s", LogEmoji.ERROR, type(exc).__name__)
            return False


class Checker:
    def __init__(self, config: Config):
        self.config = config
        self.results: List[CheckinResult] = []

    def checkin_all(self) -> None:
        for cookie_index, cookie in enumerate(self.config.cookies_list, 1):
            exchange_attempted = False
            for domain in self.config.domains:
                logger.info(
                    "%s 开始处理 %s[%s] %s[%s]。",
                    LogEmoji.START,
                    LogEmoji.COOKIE,
                    cookie_index,
                    LogEmoji.DOMAIN,
                    domain,
                )
                result = self._checkin_on_domain(cookie, cookie_index, domain, not exchange_attempted)
                exchange_attempted = exchange_attempted or result.exchange_attempted
                self.results.append(result)

    def _checkin_on_domain(self, cookie: str, cookie_index: int, domain: str, allow_exchange: bool = True) -> CheckinResult:
        result = CheckinResult(cookie_index, domain)
        with API(domain, cookie_index, verbose=self.config.verbose) as api:
            result.days, status_code = api.get_status(cookie)
            if status_code != 0:
                result.status = "认证或状态查询失败"
                result.api_code = getattr(api, "last_api_code", status_code)
                result.diagnostic = getattr(api, "last_error", "") or f"状态查询失败，code={status_code}"
                result.exchange = "未兑换: 未确认登录状态"
                return result

            checkin = api.checkin(cookie)
            result.status = str(checkin["status"])
            result.points = str(checkin["points"])
            result.code = checkin.get("code", CheckinStatus.FAILURE)  # type: ignore[assignment]
            result.api_code = checkin.get("api_code")  # type: ignore[assignment]
            result.attempts = int(checkin.get("attempts", 1))
            if result.code == CheckinStatus.FAILURE:
                result.diagnostic = str(checkin.get("message", "未知业务错误"))
                result.exchange = "未兑换: 签到失败"
                return result

            result.points_total, points_number = api.get_points(cookie)
            if points_number is None:
                result.query_failed = True
                result.api_code = getattr(api, "last_api_code", None)
                result.exchange = "未兑换: 积分查询失败"
                result.diagnostic = getattr(api, "last_error", "") or "积分查询失败"
                return result
            if self.config.exchange_plan == ExchangePlan.OFF.value:
                return result
            if result.code == CheckinStatus.REPEAT:
                result.exchange = "未兑换: 本次为重复签到"
                return result
            if not allow_exchange:
                result.exchange = "未兑换: 本轮该 Cookie 已尝试兑换"
                return result

            required_points = self.config.EXCHANGE_PLANS[self.config.exchange_plan]
            if points_number < required_points:
                result.exchange = f"未兑换: {points_number}/{required_points} 积分"
                return result

            result.exchange_attempted = True
            result.exchange, exchange_ok = api.exchange(cookie, self.config.exchange_plan)
            result.exchange_failed = not exchange_ok
            if not exchange_ok:
                result.diagnostic = getattr(api, "last_error", "") or result.exchange
            return result

    def has_failures(self) -> bool:
        return not self.results or any(
            result.code == CheckinStatus.FAILURE or result.exchange_failed or result.query_failed
            for result in self.results
        )

    def format_results(self) -> Tuple[str, str, str]:
        success_count = sum(result.code == CheckinStatus.SUCCESS for result in self.results)
        repeat_count = sum(result.code == CheckinStatus.REPEAT for result in self.results)
        fail_count = sum(result.code == CheckinStatus.FAILURE for result in self.results)
        exchange_fail_count = sum(result.exchange_failed for result in self.results)
        query_fail_count = sum(result.query_failed for result in self.results)

        title = (
            f"GLaDOS 签到: 成功 {success_count}, 今日已签到 {repeat_count}, "
            f"失败 {fail_count}, 查询失败 {query_fail_count}, 兑换失败 {exchange_fail_count}"
        )
        detail_lines = [
            (
                f"#{result.cookie_index} {result.domain} {result.status} | 获得 {result.points} | "
                f"剩余 {result.days} | 总计 {result.points_total} | {result.exchange}"
                + (f" | 原因: {result.diagnostic}" if result.diagnostic else "")
            )
            for result in self.results
        ]
        content = "\n".join(detail_lines)
        log_content = content if self.config.verbose else "\n".join(
            f"#{result.cookie_index} {result.domain} {result.status} | {result.exchange}"
            + (f" | 原因: {result.diagnostic}" if result.diagnostic else "")
            for result in self.results
        )
        return title, content, log_content

    def report_actions(self) -> None:
        """输出可读摘要和失败注解；写入的诊断已在 API 边界脱敏。"""
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            lines = ["## GLaDOS 签到结果", "", "| Cookie 序号 | 域名 | 结果 | API code | 尝试次数 | 兑换结果 | 原因 |", "|---|---|---|---|---|---|---|"]
            for result in self.results:
                cells = [str(result.cookie_index), result.domain, result.status, str(result.api_code) if result.api_code is not None else "—", str(result.attempts), result.exchange, result.diagnostic or "—"]
                escaped = [cell.replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ").replace("\r", " ") for cell in cells]
                lines.append("| " + " | ".join(escaped) + " |")
            with Path(summary_path).open("a", encoding="utf-8") as summary:
                summary.write("\n".join(lines) + "\n")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            for result in self.results:
                if result.code == CheckinStatus.FAILURE or result.exchange_failed or result.query_failed:
                    message = f"Cookie #{result.cookie_index} {result.domain}: {result.diagnostic or result.status}"
                    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                    print(f"::error title=GLaDOS 签到失败::{escaped}")


logger = init_logger()


def main() -> int:
    """执行签到，并用退出码向 GitHub Actions 报告真实结果。"""

    config: Optional[Config] = None
    title = "GLaDOS 签到失败"
    content = "脚本尚未开始。"
    exit_code = 1

    try:
        config = Config()
        checker = Checker(config)
        checker.checkin_all()
        title, content, log_content = checker.format_results()
        checker.report_actions()
        logger.info("%s ===== 签到总结 =====\n%s\n%s", LogEmoji.END, title, log_content)
        exit_code = 1 if checker.has_failures() else 0
    except Exception as exc:
        cookies = ";".join(config.cookies_list) if config else os.environ.get(Config.ENV_COOKIES, "")
        push_key = config.push_key if config else os.environ.get(Config.ENV_PUSH_KEY, "")
        content = f"{type(exc).__name__}: {safe_text(exc, cookies, (push_key,))}"
        logger.error("%s 主程序执行失败: %s", LogEmoji.ERROR, content)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            escaped = content.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::error title=GLaDOS 运行失败::{escaped}")
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as summary:
                summary.write("## GLaDOS 运行失败\n\n" + content.replace("`", "").replace("<", "&lt;") + "\n")

    push_key = config.push_key if config is not None else os.environ.get(Config.ENV_PUSH_KEY, "")
    PushService(push_key).send(title, content)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
