"""客户端门面测试:缓存管线、单飞、属性糖与同步桥行为。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from freesignt.core.cache import TTLCache
from freesignt.core.client import AsyncFreeSight, FreeSight
from freesignt.core.errors import SyncClientInAsyncContextError
from tests.conftest import RouterTransport, install_default_search_routes


def _count_calls(router: RouterTransport, fragment: str) -> int:
    """统计命中指定 URL 子串的请求数。"""
    return sum(1 for r in router.calls if fragment in str(r.url))


def test_root_exports_both_clients() -> None:
    """根包同时导出同步与异步双客户端(同层级 API,防止被误收窄)。"""
    import freesignt

    assert "FreeSight" in freesignt.__all__
    assert "AsyncFreeSight" in freesignt.__all__
    assert freesignt.AsyncFreeSight is AsyncFreeSight
    assert freesignt.FreeSight is FreeSight


async def test_cache_hit_marks_cached(client, router) -> None:
    """第二次同参调用命中缓存,不再发网络请求。"""
    router.add_json("v2ex.com/api/topics/hot", [{"title": "t"}])
    first = await client.fetch("v2ex")
    second = await client.fetch("v2ex")
    assert first.ok and second.ok
    assert first.cached is False
    assert second.cached is True
    assert _count_calls(router, "topics/hot") == 1


async def test_refresh_bypasses_cache(client, router) -> None:
    """refresh=True 强制打网络。"""
    router.add_json("v2ex.com/api/topics/hot", [{"title": "t"}])
    await client.fetch("v2ex")
    await client.fetch("v2ex", refresh=True)
    assert _count_calls(router, "topics/hot") == 2


async def test_ttl_override_and_zero_ttl(client, router) -> None:
    """ttl 覆盖:0 表示本次不缓存。"""
    router.add_json("v2ex.com/api/topics/hot", [{"title": "t"}])
    await client.fetch("v2ex", ttl=0)
    second = await client.fetch("v2ex", ttl=0)
    assert second.cached is False
    assert _count_calls(router, "topics/hot") == 2


async def test_failure_not_cached(client, router) -> None:
    """失败结果不进缓存,恢复后可重试成功(首两次 500 含引擎的一次重试)。"""
    state = {"hits": 0}

    def handler(request):
        state["hits"] += 1
        # v2ex 源 max_retries=2:首次 + 2 次重试共 3 次尝试全部 500 后才失败。
        if state["hits"] <= 3:
            return httpx.Response(500, text="broken")
        return httpx.Response(200, json=[{"title": "t"}])

    router.add(lambda req: "topics/hot" in str(req.url), handler)
    first = await client.fetch("v2ex", refresh=True)
    assert first.ok is False
    second = await client.fetch("v2ex")
    assert second.ok is True and second.cached is False


async def test_refresh_and_ttl_are_pipeline_params(client, router) -> None:
    """refresh/ttl 为管线关键字-only 形参,控制缓存行为而非透传给源。"""
    router.add_json("v2ex.com/api/topics/hot", [{"title": "t"}])
    await client.fetch("v2ex")
    assert _count_calls(router, "topics/hot") == 1
    refreshed = await client.fetch("v2ex", refresh=True)
    assert refreshed.cached is False
    assert _count_calls(router, "topics/hot") == 2
    zero_ttl = await client.fetch("v2ex", ttl=0)
    assert zero_ttl.cached is False
    assert _count_calls(router, "topics/hot") == 3


async def test_singleflight_via_client(client, router) -> None:
    """并发 10 个同参 fetch 只打一次网络。"""
    import httpx

    started = asyncio.Event()

    def slow(request):
        started.set()
        return httpx.Response(200, json=[{"title": "t"}])

    router.add(lambda req: "topics/hot" in str(req.url), slow)
    results = await asyncio.gather(*(client.fetch("v2ex") for _ in range(10)))
    assert all(r.ok for r in results)
    assert _count_calls(router, "topics/hot") == 1


async def test_attribute_sugar(client, router) -> None:
    """源名即方法:client.itunes_search(...)。"""
    router.add_json("itunes.apple.com/search", {"resultCount": 0, "results": []})
    result = await client.itunes_search(term="x")
    assert result.ok and result.source == "itunes_search"
    with pytest.raises(AttributeError, match="未知数据源"):
        client.no_such_source  # noqa: B018


async def test_external_cache_injection() -> None:
    """注入外部 CacheProtocol 实现(存储外包给调用方)。"""
    router = RouterTransport()
    router.add_json("v2ex.com/api/topics/hot", [{"title": "t"}])

    class DictCache(TTLCache):
        """以 dict 观测写入行为的最小缓存替身。"""

    cache = DictCache(maxsize=8)
    async with AsyncFreeSight(transport=router, cache=cache) as client:
        await client.fetch("v2ex")
        assert len(cache) == 1
        second = await client.fetch("v2ex")
        assert second.cached is True


def test_sync_client_end_to_end() -> None:
    """同步门面:fetch/search/上下文管理/线程桥全部可用。"""
    router = RouterTransport()
    install_default_search_routes(router)
    with FreeSight(transport=router, rate_multiplier=1000.0) as client:
        result = client.hn_algolia(query="notion")
        assert result.ok and result.source == "hn_algolia"
        agg = client.search("notion")
        assert set(agg.ok_sources) == {
            "itunes_search", "hn_algolia", "github_public", "npm_registry",
            "pypi_metadata", "huggingface_hub", "bluesky", "uspto_trademark",
            "steam_store",
        }
        snapshot = client.rate_snapshot()
        assert snapshot  # 各 host 治理器已建立


def test_sync_client_close_idempotent() -> None:
    """close 幂等,可重复调用。"""
    router = RouterTransport()
    client = FreeSight(transport=router)
    client.close()
    client.close()


async def test_sync_client_in_async_context_rejected() -> None:
    """运行中的事件循环内禁止同步门面。"""
    client = FreeSight(transport=RouterTransport())
    try:
        coro = client.async_client.fetch("v2ex")
        with pytest.raises(SyncClientInAsyncContextError):
            client._bridge.run(coro)
        coro.close()  # 桥接在提交前即拒绝,手动关闭协程避免未等待告警
    finally:
        client.close()


def test_sync_client_thread_safety() -> None:
    """多线程并发使用同一同步客户端(事件循环桥线程安全)。"""
    from concurrent.futures import ThreadPoolExecutor

    router = RouterTransport()
    install_default_search_routes(router)
    client = FreeSight(transport=router, rate_multiplier=1000.0)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(client.fetch, "hn_algolia", query="x") for _ in range(8)]
            results = [f.result() for f in futures]
        assert all(r.ok for r in results)
    finally:
        client.close()
