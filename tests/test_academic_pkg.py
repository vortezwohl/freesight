"""学术文献与包生态类源的离线单测(MockTransport)。

覆盖:Crossref/OpenAlex/arXiv、RubyGems/crates/Packagist/NuGet/
Docker Hub/Repology 的双模式(详情/搜索)。
"""

from __future__ import annotations

import pytest

ATOM_DOC = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>ArXiv</title>
<entry><title>Paper One</title>
<link rel="alternate" href="https://arxiv.org/abs/1"/>
<published>2026-01-01T00:00:00Z</published><summary>Abstract.</summary></entry>
</feed>"""


# ---- 学术文献 ----------------------------------------------------------------


async def test_crossref_normalization(client, router) -> None:
    """Crossref message 结构裁剪。"""
    router.add_json("api.crossref.org/works", {
        "message": {"total-results": 10, "items": [{"DOI": "10.1/x"}]},
    })
    result = await client.fetch("crossref", query="attention", mailto="a@b.c")
    assert result.ok and result.data["total"] == 10
    assert "mailto=a" in str(router.calls[-1].url)


async def test_openalex_entity_guard_and_data(client, router) -> None:
    """entity 枚举校验与 meta.count 归一化。"""
    with pytest.raises(ValueError):
        await client.fetch("openalex", search="x", entity="journals")

    router.add_json("api.openalex.org/works",
                    {"meta": {"count": 14904071}, "results": [{"id": "W1"}]})
    result = await client.fetch("openalex", search="attention")
    assert result.ok and result.data["total"] == 14904071
    assert result.data["results"][0]["id"] == "W1"


async def test_arxiv_atom_feed(client, router) -> None:
    """arXiv Atom 解析为 feed(命名空间通配)。"""
    router.add_text("export.arxiv.org/api/query", ATOM_DOC,
                    content_type="application/atom+xml")
    result = await client.fetch("arxiv", search_query="all:electron")
    assert result.ok and result.data["items"][0]["link"] == "https://arxiv.org/abs/1"


async def test_arxiv_garbage_body(client, router) -> None:
    """坏 XML 显式失败。"""
    router.add_text("export.arxiv.org/api/query", "garbage")
    bad = await client.fetch("arxiv", search_query="all:electron")
    assert not bad.ok and "Atom" in bad.error


# ---- 包生态 ------------------------------------------------------------------


async def test_rubygems_dual_mode(client, router) -> None:
    """gem 详情与搜索双模式。"""
    router.add_json("rubygems.org/api/v1/gems/rails", {"name": "rails"})
    result = await client.fetch("rubygems", name="rails")
    assert result.ok and result.data["name"] == "rails"

    router.add_json("rubygems.org/api/v1/search.json", [{"name": "rails"}])
    search = await client.fetch("rubygems", search="rails", refresh=True)
    assert search.ok and search.data[0]["name"] == "rails"


async def test_crates_dual_mode(client, router) -> None:
    """crate 详情透传与搜索裁剪。"""
    router.add_json("crates.io/api/v1/crates/serde", {"crate": {"name": "serde"}})
    result = await client.fetch("crates", crate="serde")
    assert result.ok and result.data["crate"]["name"] == "serde"

    router.add_json("crates.io/api/v1/crates",
                    {"crates": [{"name": "serde"}], "meta": {"total": 1}})
    search = await client.fetch("crates", search="serde", refresh=True)
    assert search.ok and search.data["total"] == 1


async def test_packagist_dual_mode(client, router) -> None:
    """包详情与搜索。"""
    router.add_json("packagist.org/packages/monolog/monolog.json",
                    {"package": {"name": "monolog/monolog"}})
    result = await client.fetch("packagist", package="monolog/monolog")
    assert result.ok and result.data["package"]["name"] == "monolog/monolog"

    router.add_json("packagist.org/search.json", {"results": [{"name": "monolog"}]})
    search = await client.fetch("packagist", search="log", refresh=True)
    assert search.ok and search.data["total"] == 1


async def test_nuget_dual_mode(client, router) -> None:
    """版本清单(小写化)与搜索服务。"""
    router.add_json("v3-flatcontainer/newtonsoft.json/index.json",
                    {"versions": ["13.0.1", "13.0.2"]})
    result = await client.fetch("nuget", package_id="Newtonsoft.Json")
    assert result.ok and result.data["versions"] == ["13.0.1", "13.0.2"]

    router.add_json("azuresearch-usnc.nuget.org/query",
                    {"totalHits": 9, "data": [{"id": "Newtonsoft.Json"}]})
    search = await client.fetch("nuget", search="json", refresh=True)
    assert search.ok and search.data["total"] == 9


async def test_dockerhub_dual_mode(client, router) -> None:
    """仓库详情与镜像搜索。"""
    router.add_json("hub.docker.com/v2/repositories/library/nginx/",
                    {"name": "nginx", "pull_count": 100})
    result = await client.fetch("dockerhub", repo="library/nginx")
    assert result.ok and result.data["pull_count"] == 100

    router.add_json("hub.docker.com/v2/search/repositories/",
                    {"count": 2, "results": [{"repo_name": "nginx"}]})
    search = await client.fetch("dockerhub", search="nginx", refresh=True)
    assert search.ok and search.data["count"] == 2


async def test_repology_entries(client, router) -> None:
    """跨仓库版本聚合裁剪。"""
    router.add_json("repology.org/api/v1/project/firefox",
                    [{"repo": "debian", "version": "128", "status": "newest"},
                     {"repo": "freebsd", "version": "128"}])
    result = await client.fetch("repology", project="firefox")
    assert result.ok and result.data["count"] == 2
    assert result.data["entries"][0]["repo"] == "debian"
