"""注册表与源元信息测试:全量注册、唯一性、分类与元信息完整性。"""

from __future__ import annotations

import freesignt  # noqa: F401  (导入触发注册)
from freesignt.core import registry
from freesignt.core.errors import SourceNotFoundError
from freesignt.core.models import SourceCategory


def test_all_74_sources_registered() -> None:
    """74 个源全部注册且名称唯一(uspto_trademark 因上游迁 key 已移除)。"""
    names = registry.names()
    assert len(names) == 74
    assert len(set(names)) == 74


def test_expected_source_names_present() -> None:
    """关键源名称齐全(按 sources 各模块逐组核对)。"""
    expected = {
        # itunes
        "itunes_search", "itunes_reviews", "itunes_charts",
        # hackernews
        "hn_algolia", "hn_firebase",
        # github
        "github_public",
        # dev_ecosystem
        "npm_registry", "pypi_metadata", "pypi_downloads",
        "ecosyste_ms", "wordpress_plugins", "huggingface_hub",
        # jobs
        "greenhouse_jobs", "lever_jobs",
        # social
        "bluesky", "mastodon_trends", "v2ex",
        # gov_registry
        "sec_edgar", "rdap_domain", "common_crawl",
        # steam / misc
        "steam_store", "steamspy", "itchio_feed", "crt_sh",
        # infra_intel / threat_intel
        "rapiddns", "subdomain_center", "otx_passive_dns", "hackertarget",
        "shodan_internetdb", "certspotter", "wayback_cdx",
        "urlscan", "hudsonrock",
        # search_engines
        "ddg_search", "searxng", "baidu", "yahoo", "mojeek",
        "wikipedia", "wikidata", "gdelt",
        # web_fetch
        "jina_reader", "allorigins", "codetabs", "corsproxy",
        # corp_registry
        "jp_houjin_bangou", "fr_sirene", "fr_bodacc", "no_brreg", "fdic_banks",
        # venture
        "yc_companies", "sec_form_d", "signal_nfx",
        # macro_stats
        "worldbank", "eurostat", "oecd", "imf", "cn_stats",
        # product_community
        "discourse", "fdroid",
        # social_feed
        "reddit", "youtube_rss", "google_news", "rsshub", "lobsters",
        # academic
        "crossref", "openalex", "arxiv",
        # pkg_ecosystem
        "rubygems", "crates", "packagist", "nuget", "dockerhub", "repology",
    }
    assert expected == set(registry.names())


def test_all_free_nokey_category() -> None:
    """当前全部源归入 free_nokey 分类。"""
    sources = registry.by_category(SourceCategory.FREE_NOKEY)
    assert len(sources) == 74


def test_get_unknown_raises_with_hint() -> None:
    """未知源名报 SourceNotFoundError 并附带可用源提示。"""
    try:
        registry.get("nonexistent_source")
    except SourceNotFoundError as exc:
        assert "nonexistent_source" in str(exc)
        assert "itunes_search" in exc.available
    else:
        raise AssertionError("应当抛出 SourceNotFoundError")


def test_source_info_complete() -> None:
    """SourceInfo 元信息字段完整且 schema 可用。"""
    for info in registry.catalogue():
        assert info.name
        assert info.category == SourceCategory.FREE_NOKEY
        assert info.description
        assert info.rate_limit > 0 and info.rate_period_s > 0
        assert info.timeout >= 5
        schema = info.input_schema
        assert schema["type"] == "object"
        assert isinstance(schema["properties"], dict)
        assert isinstance(schema["required"], list)


def test_duplicate_name_rejected() -> None:
    """同名源重复注册被拒绝(子类定义阶段即报错)。"""

    cls = registry.get("itunes_search")
    try:

        class Clash(cls):  # type: ignore[misc, valid-type]
            name = "itunes_search"
            category = SourceCategory.FREE_NOKEY

    except ValueError as exc:
        assert "名称冲突" in str(exc)
    else:
        raise AssertionError("子类定义阶段应当抛出名称冲突异常")
