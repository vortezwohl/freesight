"""注册表与源元信息测试:全量注册、唯一性、分类与元信息完整性。"""

from __future__ import annotations

import freesignt  # noqa: F401  (导入触发注册)
from freesignt.core import registry
from freesignt.core.errors import SourceNotFoundError
from freesignt.core.models import SourceCategory


def test_all_25_sources_registered() -> None:
    """34 个源全部注册且名称唯一。"""
    names = registry.names()
    assert len(names) == 34
    assert len(set(names)) == 34


def test_expected_source_names_present() -> None:
    """关键源名称齐全(按原 datasource 模块逐组抽查)。"""
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
        "sec_edgar", "uspto_trademark", "rdap_domain", "common_crawl",
        # steam / misc
        "steam_store", "steamspy", "itchio_feed", "crt_sh",
        # infra_intel / threat_intel
        "rapiddns", "subdomain_center", "otx_passive_dns", "hackertarget",
        "shodan_internetdb", "certspotter", "wayback_cdx",
        "urlscan", "hudsonrock",
    }
    assert expected == set(registry.names())


def test_all_free_nokey_category() -> None:
    """当前全部源归入 free_nokey 分类。"""
    sources = registry.by_category(SourceCategory.FREE_NOKEY)
    assert len(sources) == 34


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
        assert isinstance(info.searchable, bool)
        schema = info.input_schema
        assert schema["type"] == "object"
        assert isinstance(schema["properties"], dict)
        assert isinstance(schema["required"], list)


def test_default_search_sources() -> None:
    """默认扇出集合为声明的检索型源(9 个)。"""
    defaults = registry.default_search_sources()
    assert len(defaults) == 9
    assert all(cls.search_kwarg for cls in defaults)
    assert {cls.name for cls in defaults} == {
        "itunes_search", "hn_algolia", "github_public", "npm_registry",
        "pypi_metadata", "huggingface_hub", "bluesky", "uspto_trademark",
        "steam_store",
    }


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
