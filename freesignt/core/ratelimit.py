"""异步限流引擎:令牌桶、自适应冷却与限速头解析。

面向 C 端高并发的三层防护设计(每个限流键默认取源 URL 的 host,同一
host 的多个源共享预算,与免费 API 的服务级限速语义一致):
1. 令牌桶(TokenBucket): 把源的实测限速值换算为匀速放行,天然削峰;
2. 并发信号量: 限制同一 host 的在途请求数,避免连接风暴;
3. 自适应冷却(penalize): 命中 429/Retry-After 或限速头剩余为 0 时,
   把该键整体冷却到恢复时间,所有等待者自动排队,而非继续撞墙。

所有等待均为 asyncio 协作式等待,不会阻塞事件循环与其他源。
"""

from __future__ import annotations

import asyncio
import email.utils
import math
import time
from collections.abc import Mapping
from typing import Final

# 限速头解析出的恢复时间若超过该上限,按上限冷却,防止异常头把源"冻死"。
MAX_COOLDOWN_S: Final[float] = 600.0
# 冷却时间下限:过短的冷却没有排队意义。
MIN_COOLDOWN_S: Final[float] = 1.0


class TokenBucket:
    """异步令牌桶:匀速补充令牌,acquire 排队获取。

    等待在锁内 sleep 实现:多个等待者按锁队列 FIFO 公平排队,醒来时按
    单调时钟补充令牌,无需额外条件变量。burst=1 时等价于"最小间隔节流",
    对免费 API 最友好。

    Attributes:
        rate: 每秒补充的令牌数。
        capacity: 桶容量(允许的突发额度)。
    """

    def __init__(self, rate: float, capacity: float = 1.0) -> None:
        """初始化令牌桶。

        Args:
            rate: 每秒令牌补充速率,必须为正。
            capacity: 桶容量;初始即满,允许首个请求立即通过。

        Raises:
            ValueError: rate 或 capacity 非正。
        """
        if rate <= 0 or capacity <= 0:
            raise ValueError(f"令牌桶参数必须为正: rate={rate}, capacity={capacity}")
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        """获取指定数量令牌,不足时按补齐所需时间排队等待。

        Args:
            tokens: 需要的令牌数(通常为 1)。
        """
        if tokens <= 0:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.rate
                )
                self._updated = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                # 令牌不足:睡到可补齐为止(持锁睡,后续等待者在锁上排队)。
                await asyncio.sleep((tokens - self._tokens) / self.rate)


class RateGovernor:
    """单个限流键(host)的完整治理器:并发信号量 + 冷却 + 令牌桶。

    获取顺序为 信号量 -> 冷却检查 -> 令牌桶:先限制在途数量,再消耗
    速率预算,避免出现"令牌已扣但请求压在信号量上"的预算浪费。

    冷却时间为单调时钟时间戳;等待者分片轮询冷却结束点,期间若冷却被
    延长(penalize 再次触发),下一轮循环会自动读到新值。
    """

    def __init__(
        self,
        rate_per_s: float,
        *,
        burst: float = 1.0,
        max_concurrency: int = 5,
        cooldown_poll_s: float = 5.0,
    ) -> None:
        """初始化治理器。

        Args:
            rate_per_s: 每秒允许的请求数(令牌补充速率)。
            burst: 令牌桶容量(突发额度,默认 1 即纯匀速)。
            max_concurrency: 该键最大在途请求数。
            cooldown_poll_s: 冷却等待的分片轮询间隔。
        """
        self.bucket = TokenBucket(rate_per_s, capacity=burst)
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._cooldown_until = 0.0
        self._poll_s = cooldown_poll_s

    @property
    def cooldown_remaining_s(self) -> float:
        """当前剩余冷却秒数(无冷却为 0)。"""
        return max(0.0, self._cooldown_until - time.monotonic())

    async def acquire(self) -> None:
        """获取一个执行席位(信号量+冷却+令牌),发送请求前调用。"""
        await self._semaphore.acquire()
        try:
            while True:
                remaining = self._cooldown_until - time.monotonic()
                if remaining <= 0:
                    break
                # 分片睡:若冷却被延长,循环重读最新值;若被缩短也能及时醒。
                await asyncio.sleep(min(remaining, self._poll_s))
            await self.bucket.acquire(1)
        except BaseException:
            # 获取途中被取消必须归还信号量,否则配额泄漏。
            self._semaphore.release()
            raise

    def release(self) -> None:
        """请求结束(无论成败)后归还并发席位。"""
        self._semaphore.release()

    def penalize(self, seconds: float) -> float:
        """把该键冷却指定秒数(429/限速耗尽时调用),返回生效的冷却值。

        Args:
            seconds: 期望冷却秒数;会被夹在 [MIN_COOLDOWN_S, MAX_COOLDOWN_S]。

        Returns:
            实际生效的冷却秒数。
        """
        seconds = min(max(seconds, MIN_COOLDOWN_S), MAX_COOLDOWN_S)
        target = time.monotonic() + seconds
        # 只延长不缩短:并发的多个 429 取最保守值。
        if target > self._cooldown_until:
            self._cooldown_until = target
        return seconds

    def penalize_until(self, wall_epoch: float) -> float | None:
        """冷却到某个墙钟时间戳(限速头 Reset 为 epoch 时使用)。

        Args:
            wall_epoch: 目标 Unix 时间戳(秒)。

        Returns:
            实际冷却秒数;若换算后已过期则返回 None。
        """
        seconds = wall_epoch - time.time()
        if seconds <= 0:
            return None
        return self.penalize(seconds)


class GovernorRegistry:
    """限流键 -> RateGovernor 的注册表(进程内按客户端实例隔离)。

    同一 host 多个源声明不同速率时,取最严格(最小)速率,保证共享预算
    不超过任一源声明的上限。
    """

    def __init__(self, rate_multiplier: float = 1.0, max_concurrency: int = 5) -> None:
        """初始化注册表。

        Args:
            rate_multiplier: 全局限速缩放系数;<1 更保守(如 0.3 适合
                C 端大规模部署),>1 仅在确有富余时使用。
            max_concurrency: 每个键的默认最大并发。
        """
        self._multiplier = rate_multiplier
        self._max_concurrency = max_concurrency
        self._governors: dict[str, RateGovernor] = {}

    def get_or_create(
        self,
        key: str,
        rate_per_s: float,
        *,
        burst: float = 1.0,
    ) -> RateGovernor:
        """取或建指定键的治理器;已存在时速率取更严格一方。

        Args:
            key: 限流键(通常为 host)。
            rate_per_s: 该源声明的每秒请求数。
            burst: 令牌桶容量。

        Returns:
            该键的 RateGovernor。
        """
        rate = rate_per_s * self._multiplier
        gov = self._governors.get(key)
        if gov is None:
            gov = RateGovernor(
                rate, burst=burst, max_concurrency=self._max_concurrency
            )
            self._governors[key] = gov
        elif rate < gov.bucket.rate:
            # 共享键遇到更严格的声明:收紧现有桶速率(容量不变)。
            gov.bucket.rate = rate
        return gov

    def snapshot(self) -> dict[str, dict[str, float]]:
        """全部键的观测快照(速率/剩余冷却),用于诊断与监控。"""
        return {
            key: {
                "rate_per_s": gov.bucket.rate,
                "cooldown_remaining_s": round(gov.cooldown_remaining_s, 3),
            }
            for key, gov in self._governors.items()
        }


def parse_retry_after(value: str | None) -> float | None:
    """解析 Retry-After 头(秒数或 HTTP 日期)。

    Args:
        value: 头原始值。

    Returns:
        相对当前时刻还需等待的秒数;无法解析时返回 None。
    """
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        target = email.utils.parsedate_to_datetime(value).timestamp()
        return max(0.0, target - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


def parse_rate_limit_headers(headers: Mapping[str, str]) -> dict[str, float]:
    """解析常见 X-RateLimit-* 头族,返回标准化观测值。

    兼容两类语义:GitHub/ecosyste.ms 风格(Reset 为 epoch 秒)与
    简单倒计时风格(Reset 为相对秒),通过数量级启发式区分。

    Args:
        headers: 响应头(大小写不敏感的 Mapping)。

    Returns:
        {"remaining": float, "reset_in_s": float} 中的可用子集。
    """
    out: dict[str, float] = {}

    def _num(key: str) -> float | None:
        raw = headers.get(key)
        if raw is None:
            return None
        try:
            parsed = float(raw)
        except ValueError:
            return None
        return parsed if not math.isnan(parsed) else None

    # 注意:剩余数 0 是有效观测值,必须用 None 判断而非布尔或,否则 0 会被
    # 误判为"头缺失",预防性冷却失效。
    remaining = _num("x-ratelimit-remaining")
    if remaining is None:
        remaining = _num("x-ratelimit-remaining-requests")
    if remaining is not None:
        out["remaining"] = remaining

    reset = _num("x-ratelimit-reset")
    if reset is None:
        reset = _num("x-ratelimit-reset-requests")
    if reset is not None:
        if reset > 1e8:
            # 数量级判定为 Unix epoch:换算为相对秒。
            reset_in = reset - time.time()
        else:
            reset_in = reset
        out["reset_in_s"] = max(0.0, reset_in)
    return out
