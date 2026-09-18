"""限流引擎测试:令牌桶节拍、冷却、限速头解析与引擎自适应行为。"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC

import httpx

from freesignt.core.ratelimit import (
    GovernorRegistry,
    RateGovernor,
    TokenBucket,
    parse_rate_limit_headers,
    parse_retry_after,
)
from tests.conftest import RouterTransport


async def test_token_bucket_paces_requests() -> None:
    """容量 1 的桶:两次获取间隔约为 1/rate。"""
    bucket = TokenBucket(rate=20.0, capacity=1.0)  # 间隔 0.05s
    start = time.perf_counter()
    await bucket.acquire()
    mid = time.perf_counter()
    await bucket.acquire()
    end = time.perf_counter()
    assert mid - start < 0.02  # 首个令牌立即可用
    gap = end - mid
    assert 0.03 < gap < 0.2, f"第二次获取间隔异常: {gap}"


async def test_token_bucket_burst() -> None:
    """容量 3 的桶:前 3 个请求立即通过,第 4 个需等待。"""
    bucket = TokenBucket(rate=5.0, capacity=3.0)
    start = time.perf_counter()
    for _ in range(3):
        await bucket.acquire()
    assert time.perf_counter() - start < 0.05
    await bucket.acquire()
    assert time.perf_counter() - start > 0.15


async def test_governor_cooldown_blocks_and_releases() -> None:
    """penalize 后 acquire 需等待冷却结束(MIN_COOLDOWN_S 下限生效)。"""
    gov = RateGovernor(rate_per_s=1000.0, max_concurrency=5)
    await gov.acquire()
    gov.release()
    applied = gov.penalize(1.5)
    assert applied == 1.5
    assert gov.penalize(0.3) == 1.0  # 下限:过短冷却被抬到 MIN_COOLDOWN_S
    start = time.perf_counter()
    await gov.acquire()
    waited = time.perf_counter() - start
    assert waited > 0.8, f"冷却等待不足: {waited}"
    gov.release()


async def test_governor_semaphore_bounds_concurrency() -> None:
    """并发席位限制同一时刻在途请求数。"""
    gov = RateGovernor(rate_per_s=1000.0, max_concurrency=2)
    in_flight = 0
    peak = 0

    async def worker() -> None:
        nonlocal in_flight, peak
        await gov.acquire()
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        gov.release()

    await asyncio.gather(*(worker() for _ in range(6)))
    assert peak <= 2


def test_registry_takes_stricter_rate() -> None:
    """共享键多源声明取最严格速率。"""
    registry = GovernorRegistry(rate_multiplier=1.0, max_concurrency=5)
    loose = registry.get_or_create("shared.host", rate_per_s=10.0)
    strict = registry.get_or_create("shared.host", rate_per_s=2.0)
    assert loose is strict
    assert strict.bucket.rate == 2.0


def test_parse_retry_after_variants() -> None:
    """Retry-After 三种形态:秒数/HTTP 日期/非法值。"""
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after("") is None
    assert parse_retry_after("not-a-date") is None
    http_date = email_date_after(120)
    parsed = parse_retry_after(http_date)
    assert parsed is not None and 110 < parsed < 130


def email_date_after(seconds: int) -> str:
    """构造距当前时间指定秒偏移的 HTTP 日期串。"""
    import email.utils
    from datetime import datetime, timedelta

    target = datetime.now(UTC) + timedelta(seconds=seconds)
    return email.utils.format_datetime(target)


def test_parse_rate_limit_headers_epoch_and_offset() -> None:
    """Reset 头的 epoch 与相对秒两种语义都能归一为 reset_in_s。"""
    epoch_reset = str(int(time.time()) + 120)
    headers = httpx.Headers(
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": epoch_reset}
    )
    signals = parse_rate_limit_headers(headers)
    assert signals["remaining"] == 0.0
    assert 100 < signals["reset_in_s"] < 140

    offset = parse_rate_limit_headers(
        httpx.Headers({"X-RateLimit-Remaining": "5", "X-RateLimit-Reset": "42"})
    )
    assert offset["reset_in_s"] == 42.0


async def test_engine_429_short_cooldown_retries() -> None:
    """429 且 Retry-After 短:冷却后自动重试并成功。"""
    from freesignt.core.client import AsyncFreeSight

    router = RouterTransport()
    state = {"hits": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["hits"] += 1
        if state["hits"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"ok": True})

    router.add(lambda req: "hn.algolia.com" in str(req.url), handler)
    async with AsyncFreeSight(transport=router, rate_multiplier=1000.0) as client:
        result = await client.fetch("hn_algolia", query="test")
    assert result.ok is True
    assert result.meta.get("cooldown_s") == 1.0
    assert state["hits"] == 2


async def test_engine_429_long_cooldown_returns_error() -> None:
    """429 且冷却长于重试上限:立即返回失败并携带冷却信息。"""
    from freesignt.core.client import AsyncFreeSight

    router = RouterTransport()
    router.add(
        lambda req: "store.steampowered.com" in str(req.url),
        lambda req: httpx.Response(429),
    )
    async with AsyncFreeSight(transport=router, rate_multiplier=1000.0) as client:
        result = await client.fetch("steam_store", term="hollow knight")
    assert result.ok is False
    assert result.status == 429
    assert result.meta["cooldown_s"] == 300.0  # Steam 源声明的冷却


async def test_engine_rate_limit_header_proactive_cooldown() -> None:
    """剩余配额为 0 的成功响应也会触发预防性冷却(快照可见)。"""
    from freesignt.core.client import AsyncFreeSight

    router = RouterTransport()
    router.add(
        lambda req: "api.github.com" in str(req.url),
        lambda req: httpx.Response(
            200,
            json={"full_name": "o/r"},
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "60"},
        ),
    )
    async with AsyncFreeSight(transport=router, rate_multiplier=1000.0) as client:
        result = await client.fetch("github_public", repo="o/r")
        assert result.ok is True
        snapshot = client.rate_snapshot()
        assert snapshot["api.github.com"]["cooldown_remaining_s"] > 50


async def test_engine_network_error_retries_then_fails() -> None:
    """网络异常按退避重试,耗尽后返回 ok=False 而非抛异常。"""
    from freesignt.core.client import AsyncFreeSight

    router = RouterTransport()

    def flaky(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    router.add(lambda req: "pypi.org" in str(req.url), flaky)
    async with AsyncFreeSight(
        transport=router, rate_multiplier=1000.0,
    ) as client:
        result = await client.fetch("pypi_metadata", package="requests")
    assert result.ok is False
    assert result.status is None
    assert "ConnectError" in (result.error or "")


async def test_engine_5xx_retried_once() -> None:
    """5xx 参与退避重试,恢复后成功。"""
    from freesignt.core.client import AsyncFreeSight

    router = RouterTransport()
    state = {"hits": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["hits"] += 1
        if state["hits"] == 1:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json={"data": {"last_month": 100}})

    router.add(lambda req: "pypistats.org" in str(req.url), handler)
    async with AsyncFreeSight(transport=router, rate_multiplier=1000.0) as client:
        result = await client.fetch("pypi_downloads", package="requests")
    assert result.ok is True
    assert state["hits"] == 2
