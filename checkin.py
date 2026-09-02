import json
import os
from dataclasses import asdict, dataclass
from enum import Enum
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
                exc,
            )
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

    DEFAULT_EXCHANGE_PLAN = ExchangePlan.OFF.value
    DEFAULT_VERBOSE = False

    # Cookie 只允许发送到当前官方域名，避免跨域泄露。
    DOMAINS = ("glados.one",)

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
        self._load_config()

    def _load_config(self) -> None:
        self.push_key = os.environ.get(self.ENV_PUSH_KEY, "").strip()

        raw_cookies = os.environ.get(self.ENV_COOKIES, "")
        self.cookies_list = [cookie.strip() for cookie in raw_cookies.split("&") if cookie.strip()]
        if not self.cookies_list:
            raise ValueError(f"环境变量 '{self.ENV_COOKIES}' 未包含有效 Cookie。")

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
        self.headers = {
            "origin": f"https://{domain}",
            "referer": f"https://{domain}/console/checkin",
            "user-agent": "GLaDOS-checkin/1.0 (+GitHub Actions)",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

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
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def close(self) -> None:
        self.session.close()

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
    ) -> Optional[requests.Response]:
        url = self._get_full_url(path)
        request_headers = {"cookie": cookies}

        try:
            response = self.session.request(
                method.upper(),
                url,
                headers=request_headers,
                data=data,
                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
                allow_redirects=False,
            )
        except requests.exceptions.RequestException as exc:
            self._log("error", LogEmoji.ERROR, f"请求失败: {type(exc).__name__}", force=True)
            return None

        if 300 <= response.status_code < 400:
            self._log("error", LogEmoji.ERROR, "拒绝跟随重定向。", force=True)
            return None
        if not response.ok:
            # 不打印 response.text，避免服务端错误页中的账户信息进入 Actions 日志。
            self._log("error", LogEmoji.ERROR, f"HTTP {response.status_code}。", force=True)
            return None
        return response

    @log_method
    def checkin(self, cookies: str) -> Dict[str, Union[str, CheckinStatus]]:
        response = self._make_request(
            APIEndpoint.CHECKIN.value,
            "POST",
            {"token": self.domain},
            cookies,
        )
        if response is None:
            return {
                "status": "签到失败",
                "points": "0",
                "message": "网络或 HTTP 请求失败",
                "code": CheckinStatus.FAILURE,
            }

        data = response.json()
        code = int(data.get("code", CheckinStatus.FAILURE.value))
        message = str(data.get("message", "无消息字段"))
        points = str(data.get("points", 0))

        if code == CheckinStatus.SUCCESS.value:
            return {
                "status": "签到成功",
                "points": points,
                "message": message,
                "code": CheckinStatus.SUCCESS,
            }
        if code == CheckinStatus.REPEAT.value:
            return {
                "status": "今日已签到",
                "points": "0",
                "message": message,
                "code": CheckinStatus.REPEAT,
            }
        return {
            "status": "签到失败",
            "points": "0",
            "message": message,
            "code": CheckinStatus.FAILURE,
        }

    @log_method
    def get_status(self, cookies: str) -> Tuple[str, int]:
        response = self._make_request(APIEndpoint.STATUS.value, "GET", cookies=cookies)
        if response is None:
            return "None 天", -2

        data = response.json()
        code = int(data.get("code", -2))
        status_data = data.get("data")
        left_days = status_data.get("leftDays") if isinstance(status_data, dict) else None
        if code != 0 or left_days is None:
            return "None 天", code
        return f"{int(float(left_days))} 天", code

    @log_method
    def get_points(self, cookies: str) -> Tuple[str, Optional[int]]:
        response = self._make_request(APIEndpoint.POINTS.value, "GET", cookies=cookies)
        if response is None:
            return "None 积分", None

        points = response.json().get("points")
        if points is None:
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
        return f"兑换失败: {data.get('message', '未知错误')}", False


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
            logger.error("%s PushDeer 通知失败: %s", LogEmoji.ERROR, exc)
            return False


class Checker:
    def __init__(self, config: Config):
        self.config = config
        self.results: List[CheckinResult] = []

    def checkin_all(self) -> None:
        for cookie_index, cookie in enumerate(self.config.cookies_list, 1):
            for domain in self.config.DOMAINS:
                logger.info(
                    "%s 开始处理 %s[%s] %s[%s]。",
                    LogEmoji.START,
                    LogEmoji.COOKIE,
                    cookie_index,
                    LogEmoji.DOMAIN,
                    domain,
                )
                self.results.append(self._checkin_on_domain(cookie, cookie_index, domain))

    def _checkin_on_domain(self, cookie: str, cookie_index: int, domain: str) -> CheckinResult:
        result = CheckinResult(cookie_index, domain)
        with API(domain, cookie_index, verbose=self.config.verbose) as api:
            result.days, _ = api.get_status(cookie)

            checkin = api.checkin(cookie)
            result.status = str(checkin["status"])
            result.points = str(checkin["points"])
            result.code = checkin.get("code", CheckinStatus.FAILURE)  # type: ignore[assignment]
            if result.code == CheckinStatus.FAILURE:
                result.exchange = "未兑换: 签到失败"
                return result

            result.points_total, points_number = api.get_points(cookie)
            if self.config.exchange_plan == ExchangePlan.OFF.value:
                return result
            if result.code == CheckinStatus.REPEAT:
                result.exchange = "未兑换: 本次为重复签到"
                return result

            required_points = self.config.EXCHANGE_PLANS[self.config.exchange_plan]
            if points_number is None:
                result.exchange = "未兑换: 积分查询失败"
                result.exchange_failed = True
                return result
            if points_number < required_points:
                result.exchange = f"未兑换: {points_number}/{required_points} 积分"
                return result

            result.exchange, exchange_ok = api.exchange(cookie, self.config.exchange_plan)
            result.exchange_failed = not exchange_ok
            return result

    def has_failures(self) -> bool:
        return any(
            result.code == CheckinStatus.FAILURE or result.exchange_failed
            for result in self.results
        )

    def format_results(self) -> Tuple[str, str, str]:
        success_count = sum(result.code == CheckinStatus.SUCCESS for result in self.results)
        repeat_count = sum(result.code == CheckinStatus.REPEAT for result in self.results)
        fail_count = sum(result.code == CheckinStatus.FAILURE for result in self.results)
        exchange_fail_count = sum(result.exchange_failed for result in self.results)

        title = (
            f"GLaDOS 签到: 成功 {success_count}, 今日已签到 {repeat_count}, "
            f"失败 {fail_count}, 兑换失败 {exchange_fail_count}"
        )
        detail_lines = [
            (
                f"#{result.cookie_index} {result.status} | 获得 {result.points} | "
                f"剩余 {result.days} | 总计 {result.points_total} | {result.exchange}"
            )
            for result in self.results
        ]
        content = "\n".join(detail_lines)
        log_content = content if self.config.verbose else "\n".join(
            f"#{result.cookie_index} {result.status} | {result.exchange}"
            for result in self.results
        )
        return title, content, log_content


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
        logger.info("%s ===== 签到总结 =====\n%s\n%s", LogEmoji.END, title, log_content)
        exit_code = 1 if checker.has_failures() else 0
    except Exception as exc:
        content = f"{type(exc).__name__}: {exc}"
        logger.exception("%s 主程序执行失败。", LogEmoji.ERROR)

    push_key = config.push_key if config is not None else os.environ.get(Config.ENV_PUSH_KEY, "")
    PushService(push_key).send(title, content)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
