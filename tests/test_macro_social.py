"""宏观统计与内容流类源的离线单测(MockTransport)。

覆盖:World Bank/Eurostat/OECD/IMF/中国统计、Discourse/F-Droid、
Reddit(json/rss)/YouTube RSS/Google News/RSSHub/Lobsters。
"""

from __future__ import annotations

import pytest

RSS_DOC = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title><link>https://f.example</link>
<item><title>I1</title><link>https://f.example/1</link></item>
<item><title>I2</title><link>https://f.example/2</link></item>
</channel></rss>"""


# ---- 宏观统计 ----------------------------------------------------------------


async def test_worldbank_tuple_shape(client, router) -> None:
    """世行 [meta, rows] 双元结构转 dict。"""
    router.add_json("api.worldbank.org", [{"page": 1}, [{"country": "US", "value": 1}]])
    result = await client.fetch("worldbank", indicator="NY.GDP.MKTP.CD", country="US")
    assert result.ok and result.data["meta"] == {"page": 1}
    assert result.data["data"][0]["country"] == "US"


async def test_eurostat_filters_expand(client, router) -> None:
    """filters 维度展开为查询参数并透传响应。"""
    router.add_json("eurostat/api", {"class": "dataset", "value": [1]})
    result = await client.fetch("eurostat", dataset="prc_hicp_indr",
                               filters={"geo": ["DE", "FR"], "time": "2024"})
    assert result.ok
    url = str(router.calls[-1].url)
    assert "geo=DE" in url and "FR" in url and "time=2024" in url


async def test_oecd_path_and_periods(client, router) -> None:
    """OECD 路径仅含数据集与维度;时间窗经 startPeriod/endPeriod 下推。"""
    router.add_json("sdmx.oecd.org", {"data": {"dataSets": []}})
    result = await client.fetch("oecd", dataset="DF_QNA", filter="A..B1GQ.BEL")
    assert result.ok
    assert "/data/DF_QNA/A..B1GQ.BEL" in str(router.calls[-1].url)
    await client.fetch("oecd", dataset="DF_QNA", start="2023-Q1",
                       end="2024-Q4", refresh=True)
    url = str(router.calls[-1].url)
    assert "startPeriod=2023-Q1" in url and "endPeriod=2024-Q4" in url
    # 回归防护:时间窗不得占路径 provider 段(旧实现致 403 Invalid structure)。
    assert "/2023-Q1" not in url and "/2024-Q4" not in url


async def test_imf_params(client, router) -> None:
    """IMF CompactData 透传。"""
    router.add_json("dataservices.imf.org", {"DataSet": {"Series": {}}})
    result = await client.fetch("imf", dataset="IFS", key="A.US.NGDP", start="2020")
    assert result.ok and "startPeriod=2020" in str(router.calls[-1].url)


async def test_cn_stats_passthrough(client, router) -> None:
    """国家统计局接口原样透传 returndata。"""
    router.add_json("data.stats.gov.cn", {"returncode": 200, "returndata": {}})
    result = await client.fetch("cn_stats", zb_code="A0201")
    assert result.ok and result.data["returncode"] == 200


# ---- 产品社区 ----------------------------------------------------------------


async def test_discourse_resource_and_guard(client, router) -> None:
    """resource 归一化为 .json 端点;base_url 非法报错。"""
    router.add_json("meta.discourse.org/latest.json",
                    {"users": [], "topic_list": {"topics": [{"id": 1}]}})
    result = await client.fetch("discourse", base_url="https://meta.discourse.org")
    assert result.ok and result.data["topic_list"]["topics"][0]["id"] == 1

    router.add_json("meta.discourse.org/c/feedback.json", {"users": []})
    cat = await client.fetch("discourse", base_url="https://meta.discourse.org",
                             resource="c/feedback", refresh=True)
    assert cat.ok and "c/feedback.json" in str(router.calls[-1].url)

    with pytest.raises(ValueError):
        await client.fetch("discourse", base_url="meta.discourse.org")


async def test_fdroid_package_detail(client, router) -> None:
    """F-Droid 包详情透传。"""
    router.add_json("f-droid.org/api/v1/packages/org.fdroid.fdroid",
                    {"packageName": "org.fdroid.fdroid", "packages": []})
    result = await client.fetch("fdroid", package="org.fdroid.fdroid")
    assert result.ok and result.data["packageName"] == "org.fdroid.fdroid"


# ---- 社媒/内容流 --------------------------------------------------------------


async def test_reddit_json_and_rss(client, router) -> None:
    """json 模式展平 children;rss 模式解析为 feed。"""
    router.add_json("reddit.com/r/test/hot.json", {
        "data": {"before": None, "after": "t1_", "children": [{"data": {"id": "a1"}}]},
    })
    result = await client.fetch("reddit", subreddit="test")
    assert result.ok and result.data["posts"][0]["id"] == "a1"

    router.add_text("reddit.com/r/test/top.rss", RSS_DOC)
    rss = await client.fetch("reddit", subreddit="test", listing="top", fmt="rss",
                             refresh=True)
    assert rss.ok and rss.data["count"] == 2


async def test_youtube_rss_and_guard(client, router) -> None:
    """频道 RSS 解析;channel/playlist 均缺时报错。"""
    router.add_text("youtube.com/feeds/videos.xml", RSS_DOC)
    result = await client.fetch("youtube_rss", channel_id="UCxxx", limit=1)
    assert result.ok and result.data["count"] == 1

    with pytest.raises(ValueError):
        await client.fetch("youtube_rss")


async def test_google_news_feed(client, router) -> None:
    """Google News RSS 解析并携带语言参数。"""
    router.add_text("news.google.com/rss/search", RSS_DOC)
    result = await client.fetch("google_news", query="acme corp",
                                hl="zh-CN", gl="CN", ceid="CN:zh-Hans")
    assert result.ok and result.data["items"][0]["title"] == "I1"
    assert "hl=zh-CN" in str(router.calls[-1].url)


async def test_rsshub_route_guard_and_feed(client, router) -> None:
    """route 必须以 / 开头;实例路由解析为 feed。"""
    with pytest.raises(ValueError):
        await client.fetch("rsshub", route="weibo/user/1")

    router.add_text("rsshub.app/weibo/user/1195230310", RSS_DOC)
    result = await client.fetch("rsshub", route="/weibo/user/1195230310")
    assert result.ok and result.data["count"] == 2


async def test_rsshub_invalid_body(client, router) -> None:
    """非 RSS 响应(反爬页)显式失败。"""
    router.add_text("rsshub.app/x", "<html>blocked</html>")
    result = await client.fetch("rsshub", route="/x", refresh=True)
    assert not result.ok and "RSS/Atom" in result.error


async def test_lobsters_stories(client, router) -> None:
    """Lobsters 数组裁剪。"""
    router.add_json("lobste.rs/hottest.json",
                    [{"title": "S1"}, {"title": "S2"}, {"title": "S3"}])
    result = await client.fetch("lobsters", limit=2)
    assert result.ok and result.data["count"] == 2
