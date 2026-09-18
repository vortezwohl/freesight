"""统一/语义搜索测试:扇出合并、词法排序、容错与域名情报模式。"""

from __future__ import annotations

import httpx

from tests.conftest import install_default_search_routes


async def test_default_fanout_merges_and_ranks(client, router) -> None:
    """默认 9 源扇出:命中合并、标题含查询词者优先、per_source 完整。"""
    install_default_search_routes(router)
    response = await client.search("notion")
    assert response.total > 0
    sources_seen = {h.source for h in response.hits}
    assert {"itunes_search", "hn_algolia", "github_public"} <= sources_seen
    # 词法排序:标题精确含 "notion" 的命中应显著靠前。
    top_titles = [h.title.lower() for h in response.hits[:3]]
    assert any("notion" in t for t in top_titles)
    assert set(response.per_source) == {
        "itunes_search", "hn_algolia", "github_public", "npm_registry",
        "pypi_metadata", "huggingface_hub", "bluesky", "uspto_trademark",
        "steam_store",
    }
    assert response.semantic is False
    assert response.took_s >= 0


async def test_search_limit_per_source(client, router) -> None:
    """limit_per_source 对每源命中做截断。"""
    install_default_search_routes(router)
    response = await client.search("notion", limit_per_source=1)
    per_source_counts = {s: st.count for s, st in response.per_source.items()}
    assert all(c <= 1 for c in per_source_counts.values())


async def test_search_partial_failure_tolerated(client, router) -> None:
    """单源 500 不影响整体:其余源照常返回,失败源记录错误。"""
    # 先注册 500 路由再装默认桩,确保失败路由不被同名默认路由遮蔽。
    router.add(
        lambda req: "hn.algolia.com" in str(req.url),
        lambda req: httpx.Response(500, text="down"),
    )
    install_default_search_routes(router)
    response = await client.search("notion")
    assert response.per_source["hn_algolia"].ok is False
    assert response.per_source["hn_algolia"].error
    assert response.per_source["itunes_search"].ok is True
    assert any(h.source == "itunes_search" for h in response.hits)


async def test_search_rejects_unsearchable_source(client) -> None:
    """不支持关键词扇出的源被明确拒绝并给出可用源提示。"""
    try:
        await client.search("x", sources=["v2ex"])
    except ValueError as exc:
        assert "不支持关键词扇出" in str(exc)
        assert "crt_sh" in str(exc)
    else:
        raise AssertionError("应当抛出 ValueError")


async def test_search_domain_intel_mode(client, router) -> None:
    """域名情报模式:显式指定 crt_sh/rdap_domain 做域名扇出。"""
    router.add_json("crt.sh", [{"name_value": "app.example.com"}])
    router.add_json("rdap.verisign.com", {
        "ldhName": "example.com",
        "events": [{"eventAction": "registration", "eventDate": "2024-01-01T00:00:00Z"}],
    })
    response = await client.search("example.com", sources=["crt_sh", "rdap_domain"])
    assert response.per_source["crt_sh"].ok is True
    assert response.per_source["rdap_domain"].ok is True
    titles = [h.title for h in response.hits]
    assert "app.example.com" in titles
    assert "example.com" in titles


async def test_search_hits_cache_second_time(client, router) -> None:
    """扇出请求走缓存管线:第二次搜索无新增网络请求。"""
    install_default_search_routes(router)
    await client.search("notion")
    calls_after_first = len(router.calls)
    second = await client.search("notion")
    assert len(router.calls) == calls_after_first
    assert all(st.cached for st in second.per_source.values())


async def test_semantic_rerank_with_embedder(client, router) -> None:
    """注入嵌入提供方后启用语义排序,并优于词法噪声。"""

    def fake_embedder(texts: list[str]):
        """语义玩具实现:文本含 'notion' 得到与查询同向的向量。"""
        return [[1.0, 0.0] if "notion" in t.lower() else [0.0, 1.0] for t in texts]

    install_default_search_routes(router)
    response = await client.search("notion", embedder=fake_embedder, semantic=True)
    assert response.semantic is True
    # 语义得分为余弦值 [-1,1],验证写入且有序。
    scores = [h.score for h in response.hits]
    assert scores == sorted(scores, reverse=True)
    assert any(h.score > 0.99 for h in response.hits)


async def test_semantic_embedder_failure_falls_back(client, router) -> None:
    """嵌入提供方抛异常时自动回退词法排序,不拖垮搜索。"""

    def broken_embedder(texts: list[str]):
        raise RuntimeError("embedder down")

    install_default_search_routes(router)
    response = await client.search("notion", embedder=broken_embedder)
    assert response.semantic is False
    assert response.total > 0


async def test_lexical_score_utility() -> None:
    """词法打分器:标题命中权重高于摘要命中。"""
    from freesignt.core.models import Hit
    from freesignt.core.search import score_hits_lexical

    hits = [
        Hit(source="a", title="notion app", snippet="lorem"),
        Hit(source="b", title="lorem", snippet="notion notion notion"),
        Hit(source="c", title="unrelated", snippet="unrelated"),
    ]
    score_hits_lexical("notion", hits)
    assert hits[0].score > hits[1].score > 0
    assert hits[2].score == 0


async def test_lexical_chinese_tokenization() -> None:
    """中文按单字切分:中文查询可命中标题。"""
    from freesignt.core.models import Hit
    from freesignt.core.search import score_hits_lexical

    hits = [
        Hit(source="a", title="云端笔记软件", snippet=""),
        Hit(source="b", title="游戏", snippet=""),
    ]
    score_hits_lexical("笔记", hits)
    assert hits[0].score > hits[1].score
