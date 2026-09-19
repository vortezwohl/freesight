"""聚合检索测试:多源扇出、结果聚合、容错与缓存复用(无筛选无排序)。"""

from __future__ import annotations

import httpx

from tests.conftest import install_default_search_routes

DEFAULT_SOURCE_NAMES = {
    "itunes_search", "hn_algolia", "github_public", "npm_registry",
    "pypi_metadata", "huggingface_hub", "bluesky", "uspto_trademark",
    "steam_store",
}


async def test_default_fanout_aggregates_all_sources(client, router) -> None:
    """默认 9 源扇出:results 按源分组、数据原样、键序确定。"""
    install_default_search_routes(router)
    agg = await client.search("notion")
    assert set(agg.results) == DEFAULT_SOURCE_NAMES
    assert set(agg.ok_sources) == DEFAULT_SOURCE_NAMES
    assert agg.failed_sources == []
    assert agg.took_s >= 0
    # 各源结果为该源的完整业务数据,SDK 不做任何筛选与排序。
    assert agg.results["itunes_search"].data["resultCount"] == 2
    assert agg.results["hn_algolia"].data["nbHits"] == 2
    assert agg.results["pypi_metadata"].data["info"]["name"] == "notion-client"


async def test_limit_per_source_passed_through(client, router) -> None:
    """limit_per_source 经各源 limit 参数透传到上游请求。"""
    install_default_search_routes(router)
    await client.search("notion", limit_per_source=3)
    itunes_req = next(r for r in router.calls if "itunes.apple.com/search" in str(r.url))
    assert "limit=3" in str(itunes_req.url)
    hn_req = next(r for r in router.calls if "hn.algolia.com" in str(r.url))
    assert "hitsPerPage=3" in str(hn_req.url)


async def test_partial_failure_tolerated(client, router) -> None:
    """单源 500 不影响整体:其余源照常返回,失败源保留错误详情。"""
    # 先注册 500 路由再装默认桩,确保失败路由不被同名默认路由遮蔽。
    router.add(
        lambda req: "hn.algolia.com" in str(req.url),
        lambda req: httpx.Response(500, text="down"),
    )
    install_default_search_routes(router)
    agg = await client.search("notion")
    assert agg.results["hn_algolia"].ok is False
    assert "500" in (agg.results["hn_algolia"].error or "")
    assert agg.failed_sources == ["hn_algolia"]
    assert set(agg.ok_sources) == DEFAULT_SOURCE_NAMES - {"hn_algolia"}
    assert agg.results["itunes_search"].ok is True


async def test_rejects_unsearchable_source(client) -> None:
    """不支持关键词扇出的源被明确拒绝并给出可用源提示。"""
    try:
        await client.search("x", sources=["v2ex"])
    except ValueError as exc:
        assert "不支持关键词扇出" in str(exc)
        assert "crt_sh" in str(exc)
    else:
        raise AssertionError("应当抛出 ValueError")


async def test_domain_intel_mode(client, router) -> None:
    """域名情报模式:显式指定 crt_sh/rdap_domain 做域名扇出。"""
    router.add_json("crt.sh", [{"name_value": "app.example.com"}])
    router.add_json("rdap.verisign.com", {
        "ldhName": "example.com",
        "events": [{"eventAction": "registration", "eventDate": "2024-01-01T00:00:00Z"}],
    })
    agg = await client.search("example.com", sources=["crt_sh", "rdap_domain"])
    assert list(agg.results) == ["crt_sh", "rdap_domain"]  # 键序=参与源顺序
    assert agg.results["crt_sh"].ok
    assert agg.results["crt_sh"].data["subdomains"] == ["app.example.com"]
    assert agg.results["rdap_domain"].ok
    assert agg.results["rdap_domain"].data["ldhName"] == "example.com"


async def test_fanout_uses_cache_pipeline(client, router) -> None:
    """扇出请求走 fetch 管线:第二次搜索无新增网络请求且全部标记缓存。"""
    install_default_search_routes(router)
    await client.search("notion")
    calls_after_first = len(router.calls)
    second = await client.search("notion")
    assert len(router.calls) == calls_after_first
    assert all(r.cached for r in second.results.values())


async def test_aggregate_result_to_dict(client, router) -> None:
    """AggregateResult 可整体导出为 JSON 结构。"""
    install_default_search_routes(router)
    agg = await client.search("notion", sources=["itunes_search"])
    payload = agg.to_dict()
    assert payload["query"] == "notion"
    assert payload["results"]["itunes_search"]["ok"] is True
    assert payload["results"]["itunes_search"]["data"]["resultCount"] == 2
