"""缓存与单飞测试:TTL 过期、LRU 淘汰、键稳定性、并发合并与取消重选。"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from freesignt.core.cache import NullCache, SingleFlight, TTLCache, cache_key
from freesignt.core.models import FetchResult


def _result(source: str = "test") -> FetchResult:
    """构造测试用成功结果。"""
    return FetchResult(ok=True, status=200, latency_s=0.01, data={"v": 1}, source=source)


def test_cache_key_stable_across_param_order() -> None:
    """参数顺序不同,键一致;参数不同,键不同。"""
    a = cache_key("s", {"x": 1, "y": "z"})
    b = cache_key("s", {"y": "z", "x": 1})
    c = cache_key("s", {"x": 2, "y": "z"})
    assert a == b
    assert a != c


def test_ttl_cache_expiry_with_injected_clock() -> None:
    """注入时钟验证 TTL 过期。"""
    now = 1000.0

    def tick() -> float:
        return now

    cache = TTLCache(time_fn=lambda: now)
    cache.set("k", _result(), ttl_s=10)
    assert cache.get("k") is not None
    now = 1011.0
    assert cache.get("k") is None


def test_ttl_cache_lru_eviction() -> None:
    """容量超限淘汰最久未使用条目;get 刷新热度。"""
    cache = TTLCache(maxsize=2)
    cache.set("a", _result(), ttl_s=60)
    cache.set("b", _result(), ttl_s=60)
    assert cache.get("a") is not None  # a 变为最新
    cache.set("c", _result(), ttl_s=60)  # 淘汰 b
    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.get("c") is not None


def test_ttl_cache_zero_ttl_skipped() -> None:
    """TTL<=0 不写入。"""
    cache = TTLCache()
    cache.set("k", _result(), ttl_s=0)
    assert cache.get("k") is None


def test_null_cache() -> None:
    """空缓存永远未命中。"""
    cache = NullCache()
    cache.set("k", _result(), ttl_s=60)
    assert cache.get("k") is None
    cache.clear()


async def test_singleflight_deduplicates_concurrent_calls() -> None:
    """并发同 key 调用合并为一次工厂执行。"""
    flight = SingleFlight()
    runs = 0

    async def factory() -> FetchResult:
        nonlocal runs
        runs += 1
        await asyncio.sleep(0.05)
        return _result()

    results = await asyncio.gather(*(flight.run("k", factory) for _ in range(8)))
    assert runs == 1
    assert all(r is results[0] for r in results)


async def test_singleflight_leader_cancelled_waiter_takes_over() -> None:
    """leader 被取消后,等待者接管执行并拿到结果。"""
    flight = SingleFlight()
    started = asyncio.Event()

    async def slow_factory() -> FetchResult:
        await asyncio.sleep(0.2)
        return _result()

    async def leader() -> FetchResult:
        started.set()
        return await flight.run("k", slow_factory)

    leader_task = asyncio.create_task(leader())
    await started.wait()
    # 等 leader 真正进入工厂执行后再启动等待者。
    await asyncio.sleep(0.02)
    waiter = asyncio.create_task(flight.run("k", slow_factory))
    await asyncio.sleep(0.02)
    leader_task.cancel()
    try:
        await leader_task
    except asyncio.CancelledError:
        pass
    result = await asyncio.wait_for(waiter, timeout=1.0)
    assert result.ok is True


async def test_singleflight_exception_shared() -> None:
    """工厂异常时所有共享者收到同一异常,且不残留在途条目。"""
    flight = SingleFlight()

    async def boom() -> FetchResult:
        raise RuntimeError("boom")

    outcomes = await asyncio.gather(
        flight.run("k", boom), flight.run("k", boom), return_exceptions=True
    )
    assert all(isinstance(o, RuntimeError) for o in outcomes)
    assert flight._inflight == {}


def test_fetch_result_replace_cached() -> None:
    """dataclasses.replace 复制结果并标记 cached。"""
    base = _result()
    marked = replace(base, cached=True)
    assert marked.cached is True
    assert base.cached is False
    assert marked.data == base.data
