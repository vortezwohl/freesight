"""HTTP 引擎:统一执行带限流、重试、自适应冷却的异步 GET。

职责边界:
- 本模块只做"传输治理"(限流预算、并发席位、429 冷却、限速头观测、
  指数退避重试、耗时计量),不理解任何具体源的业务语义;
- 源(BaseSource)负责声明限速参数与业务归一化,通过 _get() 间接使用本引擎;
- 与原 Industry-Research 实现的关键差异:
  1. 全异步(httpx.AsyncClient),不阻塞事件循环;
  2. 429 不再"裸重试"或原地长睡眠:先冻结该键全部流量到 Retry-After/
     冷却时间,冷却较短(<= rate_retry_cap_s)时自动重试一次,较长时
     立即返回携带冷却信息的失败结果,由调用方决策;
  3. 成功响应的 X-RateLimit-* 头实时回灌限流器,预算耗尽前主动减速,
     "既拿全数据又尽量不撞限流"。

引擎为客户端实例级组件;注入 httpx.MockTransport 即可离线测试。
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from freesignt.core.models import FetchResult
from freesignt.core.ratelimit import (
    MAX_COOLDOWN_S,
    GovernorRegistry,
    parse_rate_limit_headers,
    parse_retry_after,
)

# 默认 UA:显式申明开源研究身份,便于源方识别与联系。
DEFAULT_UA = "FreeSight/0.1 (open-source competitive research SDK)"
# SEC 系列源要求"公司名 邮箱"格式 UA,此为占位默认,生产应通过配置覆盖。
DEFAULT_SEC_UA = "FreeSightResearch admin@example.org"


def host_of(url: str) -> str:
    """取 URL 的 host 作为默认限流键。

    Args:
        url: 目标 URL。

    Returns:
        小写 host;解析失败时退回完整 URL(退化为按源隔离限流)。
    """
    try:
        return (urlsplit(url).hostname or url).lower()
    except ValueError:
        return url


@dataclass
class HttpConfig:
    """HTTP 引擎与限流策略的可调配置(客户端构造参数的载体)。

    Attributes:
        timeout_s: 默认单请求超时(源声明优先)。
        user_agent: 默认 User-Agent。
        sec_user_agent: SEC 系列源使用的申明式 UA(公司名 邮箱格式)。
        http2: 是否启用 HTTP/2(同源多路复用,降低连接压力)。
        trust_env: 是否遵循代理相关环境变量(HTTP_PROXY 等)。
        verify: 是否校验 TLS 证书。
        rate_multiplier: 全局限速缩放系数;<1 更保守(如 0.3 适合
            C 端大规模部署),>1 仅在确有富余时使用。
        max_concurrency_per_host: 每个 host 的最大在途请求数。
        max_retries_network: 网络异常(超时/连接失败)最大重试次数上限,
            与源声明值取小。
        retry_5xx: 5xx 是否参与退避重试(默认开,提升弱源成功率)。
        max_rate_retries: 429 冷却后的自动重试次数(默认 1)。
        rate_retry_cap_s: 冷却时长超过该值时不自动重试,直接返回失败
            (避免 agent 长时间挂起),错误信息携带冷却秒数。
        default_cooldown_s: 429 且无 Retry-After 时的默认冷却秒数。
        backoff_base_s: 指数退避基数。
        backoff_max_s: 单次退避上限。
        extra_headers: 附加到所有请求的默认头(与源头合并,同名后者优先)。
    """

    timeout_s: float = 20.0
    user_agent: str = DEFAULT_UA
    sec_user_agent: str = DEFAULT_SEC_UA
    http2: bool = True
    trust_env: bool = True
    verify: bool = True
    rate_multiplier: float = 1.0
    max_concurrency_per_host: int = 5
    max_retries_network: int = 3
    retry_5xx: bool = True
    max_rate_retries: int = 1
    rate_retry_cap_s: float = 30.0
    default_cooldown_s: float = 60.0
    backoff_base_s: float = 1.0
    backoff_max_s: float = 8.0
    extra_headers: dict[str, str] = field(default_factory=dict)


class HttpEngine:
    """执行层引擎:所有源的网络请求经由本类的 request() 发出。

    一个引擎实例持有一个 httpx.AsyncClient(连接复用)与一套限流键
    注册表;必须在单个事件循环内创建与使用,关闭时调用 aclose()。
    """

    def __init__(
        self,
        config: HttpConfig | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """初始化引擎。

        Args:
            config: 引擎配置;缺省用默认值。
            transport: 自定义传输层(测试注入 MockTransport 用;生产为 None)。
        """
        self.config = config or HttpConfig()
        self._transport = transport
        self.governors = GovernorRegistry(
            rate_multiplier=self.config.rate_multiplier,
            max_concurrency=self.config.max_concurrency_per_host,
        )
        self._client: httpx.AsyncClient | None = None
        self._closed = False

    @property
    def client(self) -> httpx.AsyncClient:
        """惰性创建共享的 httpx.AsyncClient(连接池复用)。"""
        if self._closed:
            raise RuntimeError("HttpEngine 已关闭,请重新构造客户端")
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout_s,
                http2=self.config.http2,
                verify=self.config.verify,
                trust_env=self.config.trust_env,
                follow_redirects=True,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        """关闭底层连接池;幂等。"""
        self._closed = True
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _backoff(self, attempt: int) -> float:
        """计算带随机抖动的指数退避秒数。

        Args:
            attempt: 当前重试轮次(1 起)。

        Returns:
            退避秒数(含抖动,封顶 backoff_max_s)。
        """
        delay = min(
            self.config.backoff_base_s * (2 ** (attempt - 1)),
            self.config.backoff_max_s,
        )
        return delay * (1.0 + random.uniform(0.0, 0.5))

    async def request(
        self,
        *,
        source: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        max_retries: int = 2,
        rate_limit: int = 60,
        rate_period_s: int = 60,
        limit_key: str | None = None,
        burst: int = 1,
        cooldown_s: float | None = None,
    ) -> FetchResult:
        """执行带完整治理的 GET 请求。

        治理顺序:并发信号量 -> 冷却检查 -> 令牌桶 -> 发送;响应后归还
        席位并回灌限速头观测;429 触发冷却,短冷却自动重试,长冷却立即
        返回失败;网络异常与 5xx 按指数退避重试。

        Args:
            source: 发起源名称(写入 FetchResult.source 与日志)。
            url: 目标 URL。
            params: 查询参数。
            headers: 源级请求头(已含 UA)。
            timeout: 本请求超时秒数;None 用引擎默认。
            max_retries: 网络异常最大重试次数(与引擎上限取小)。
            rate_limit: 源声明的限速窗口内请求数。
            rate_period_s: 限速窗口长度秒。
            limit_key: 限流键覆盖;None 按 URL host。
            burst: 令牌桶突发容量。
            cooldown_s: 该源 429 的默认冷却覆盖(如 Steam 300s)。

        Returns:
            统一 FetchResult;失败不抛异常(引擎级编程错误除外)。
        """
        gov = self.governors.get_or_create(
            limit_key or host_of(url),
            rate_limit / max(rate_period_s, 1),
            burst=burst,
        )
        net_retries = min(max_retries, self.config.max_retries_network)
        attempts_net = 0
        rate_retries = 0
        observed: dict[str, Any] = {"rate_key": limit_key or host_of(url)}

        while True:
            await gov.acquire()
            start = time.perf_counter()
            try:
                resp = await self.client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout if timeout is not None else self.config.timeout_s,
                )
            except httpx.HTTPError as exc:
                latency = time.perf_counter() - start
                if attempts_net < net_retries:
                    attempts_net += 1
                    await asyncio.sleep(self._backoff(attempts_net))
                    continue
                observed["network_attempts"] = attempts_net + 1
                return FetchResult(
                    ok=False,
                    status=None,
                    latency_s=latency,
                    error=f"{type(exc).__name__}: {exc}",
                    source=source,
                    meta=observed,
                )
            except BaseException:
                # 取消等非 HTTP 异常:不重试,原样上抛(如 asyncio.CancelledError)。
                raise
            finally:
                # 每次尝试结束(含取消/重试 continue 路径)都归还并发席位。
                gov.release()
            latency = time.perf_counter() - start

            # 成功响应也回灌限速头:预算耗尽前主动冷却,防患于未然。
            signals = parse_rate_limit_headers(resp.headers)
            if signals:
                observed["rate_limit"] = signals
                remaining = signals.get("remaining")
                if remaining is not None and remaining <= 0:
                    reset_in = signals.get("reset_in_s", self.config.default_cooldown_s)
                    gov.penalize(min(reset_in, MAX_COOLDOWN_S))

            if resp.status_code == 200:
                return FetchResult(
                    ok=True,
                    status=200,
                    latency_s=latency,
                    data=_parse_body(resp),
                    source=source,
                    meta=observed,
                )

            if resp.status_code == 429:
                wait = (
                    parse_retry_after(resp.headers.get("retry-after"))
                    or cooldown_s
                    or self.config.default_cooldown_s
                )
                applied = gov.penalize(min(wait, MAX_COOLDOWN_S))
                observed["cooldown_s"] = applied
                # 短冷却自动重试一次;长冷却立即让位给调用方决策。
                short_cooldown = applied <= self.config.rate_retry_cap_s
                if rate_retries < self.config.max_rate_retries and short_cooldown:
                    rate_retries += 1
                    continue
                return FetchResult(
                    ok=False,
                    status=429,
                    latency_s=latency,
                    error=f"HTTP 429: 已触发 {applied:.0f}s 限流冷却,建议稍后重试",
                    source=source,
                    meta=observed,
                )

            if self.config.retry_5xx and 500 <= resp.status_code < 600:
                if attempts_net < net_retries:
                    attempts_net += 1
                    await asyncio.sleep(self._backoff(attempts_net))
                    continue
                observed["server_retry_attempts"] = attempts_net + 1

            return FetchResult(
                ok=False,
                status=resp.status_code,
                latency_s=latency,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                source=source,
                meta=observed,
            )


def _parse_body(resp: httpx.Response) -> Any:
    """解析响应体:JSON 优先,失败回退原始文本。

    Args:
        resp: 成功(200)的响应对象。

    Returns:
        dict/list(JSON)或原始字符串。
    """
    try:
        return resp.json()
    except ValueError:
        return resp.text
