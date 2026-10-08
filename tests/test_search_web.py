"""搜索引擎与网页获取类源的离线单测(MockTransport)。

覆盖:ddgs 封装(伪造 DDGS 类)、SearXNG 双形态回退、三个 SERP
正则解析、Wikipedia/Wikidata/GDELT、Jina Reader 与三个内容代理、
_feed 工具的 RSS/Atom 解析。
"""

from __future__ import annotations

import pytest

from freesignt.sources._feed import parse_feed

# ---- _feed 工具 --------------------------------------------------------------


def test_parse_feed_rss2() -> None:
    """RSS 2.0 文档解析出通道与条目。"""
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <title>Ch</title><link>https://example.com</link>
      <item><title>A</title><link>https://example.com/a</link>
        <pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate>
        <description>desc-a</description></item>
      <item><title>B</title><link>https://example.com/b</link></item>
    </channel></rss>"""
    feed = parse_feed(xml)
    assert feed is not None
    assert feed["title"] == "Ch"
    assert feed["count"] == 2
    assert feed["items"][0]["published"].startswith("Mon, 01 Jan")
    assert feed["items"][1]["description"] == ""


def test_parse_feed_atom() -> None:
    """Atom 文档解析(entry 的 link 取 href)。"""
    xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Papers</title><updated>2026-01-01T00:00:00Z</updated>
      <entry><title>P1</title>
        <link rel="alternate" href="https://arxiv.org/1"/>
        <published>2026-01-01T00:00:00Z</published>
        <summary>abs text</summary></entry>
    </feed>"""
    feed = parse_feed(xml)
    assert feed is not None
    assert feed["items"][0]["link"] == "https://arxiv.org/1"
    assert feed["items"][0]["description"] == "abs text"


def test_parse_feed_garbage() -> None:
    """非 XML 输入返回 None。"""
    assert parse_feed("not xml at all") is None


# ---- ddg_search(伪造 DDGS 类)------------------------------------------------


class _FakeDDGS:
    """ddgs.DDGS 的测试替身:text/news 返回固定结果。"""

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout

    def text(self, query: str, region: str = "wt-wt", max_results: int = 10):
        return [{"title": f"T-{query}", "href": "https://ddg.example/1", "body": "B"}]

    def news(self, query: str, region: str = "wt-wt", max_results: int = 10):
        return [{"title": "N1", "url": "https://ddg.example/news", "body": "NB"}]


async def test_ddg_search_text(client, monkeypatch) -> None:
    """text 模式经 to_thread 桥接返回伪造结果。"""
    import ddgs as ddgs_pkg

    monkeypatch.setattr(ddgs_pkg, "DDGS", _FakeDDGS)
    result = await client.fetch("ddg_search", query="notion")
    assert result.ok
    assert result.data["results"][0]["title"] == "T-notion"


async def test_ddg_search_news_and_error(client, monkeypatch) -> None:
    """news 模式可用;ddgs 抛异常时转 ok=False。"""
    import ddgs as ddgs_pkg

    monkeypatch.setattr(ddgs_pkg, "DDGS", _FakeDDGS)
    result = await client.fetch("ddg_search", query="x", kind="news")
    assert result.ok and result.data["results"][0]["title"] == "N1"

    class _Boom:
        def __init__(self, timeout: int = 10) -> None:
            raise RuntimeError("ddgs down")

    monkeypatch.setattr(ddgs_pkg, "DDGS", _Boom)
    result = await client.fetch("ddg_search", query="x")
    assert not result.ok
    assert "ddgs down" in result.error


# ---- SearXNG -----------------------------------------------------------------


async def test_searxng_json_mode(client, router) -> None:
    """实例开启 JSON 时走结构化结果。"""
    router.add_json("searx.be/search", {
        "results": [{"title": "R1", "url": "https://r1.example", "engines": ["google"]}]
    })
    result = await client.fetch("searxng", query="x", instance="https://searx.be")
    assert result.ok
    assert result.data["results"][0]["url"] == "https://r1.example"


async def test_searxng_html_fallback(client, router) -> None:
    """JSON 被禁时回退解析 HTML 结果块。"""
    router.add_text(
        "searx.be/search",
        '<article><h3><a href="https://h.example/page">Hit</a></h3></article>',
        content_type="text/html",
    )
    result = await client.fetch("searxng", query="x", instance="https://searx.be")
    assert result.ok and result.data["fallback"] == "html"
    assert result.data["results"][0]["title"] == "Hit"


async def test_searxng_html_no_hits(client, router) -> None:
    """JSON 禁用且 HTML 无结果时报可读错误。"""
    router.add_text("searx.be/search", "<html><body>empty</body></html>")
    result = await client.fetch("searxng", query="x", instance="https://searx.be")
    assert not result.ok and "JSON" in result.error


async def test_searxng_antibot_page(client, router) -> None:
    """实例浏览器验证页(Anubis 类)显式失败。"""
    router.add_text(
        "searx.be/search",
        "<title>Verifying your browser…</title>",
    )
    result = await client.fetch("searxng", query="x", instance="https://searx.be")
    assert not result.ok and "浏览器验证" in result.error


# ---- SERP 源 ------------------------------------------------------------------


async def test_baidu_captcha_page(client, router) -> None:
    """安全验证页显式失败。"""
    router.add_text("baidu.com/s", "<html><title>百度安全验证</title></html>")
    bad = await client.fetch("baidu", query="x")
    assert not bad.ok and "安全验证" in bad.error


async def test_baidu_results(client, router) -> None:
    """正常 SERP 解析出结果(标签内 em 高亮清洗)。"""
    router.add_text(
        "baidu.com/s",
        '<h3 class="c-title"><a href="http://r.example/1">Not<em>ion</em></a></h3>',
    )
    result = await client.fetch("baidu", query="notion")
    assert result.ok
    assert result.data["results"][0]["title"] == "Notion"


async def test_yahoo_filters_internal_links(client, router) -> None:
    """Yahoo SERP 两段式解析(favicon 块间隔 + 外层 href)并过滤站内链接。"""
    router.add_text(
        "search.yahoo.com/search",
        '<a href="https://video.search.yahoo.com/search/video?p=x">'
        '<h3 class="title fc-x"><span>Vid</span></h3></a>'
        '<div><a class="reg" href="https://r.example/1">'
        '<div class="thmb algo-favicon"></div><span>r.example</span></div>'
        '<h3 class="title fc-y"><span>External</span></h3></a>',
    )
    result = await client.fetch("yahoo", query="x")
    assert result.ok
    assert [r["title"] for r in result.data["results"]] == ["External"]
    assert result.data["results"][0]["url"] == "https://r.example/1"


async def test_mojeek_results(client, router) -> None:
    """Mojeek SERP 的 h2 结果解析。"""
    router.add_text(
        "mojeek.com/search",
        '<h2><a href="https://r.example/1">One</a></h2><h2><a href="https://r.example/2">Two</a></h2>',
    )
    result = await client.fetch("mojeek", query="x")
    assert result.ok and result.data["count"] == 2


# ---- Wikipedia / Wikidata / GDELT ----------------------------------------------


async def test_wikipedia_search_and_summary(client, router) -> None:
    """搜索模式归一化 pages;摘要模式透传。"""
    router.add_json("rest.php/v1/search/page", {"pages": [{"id": 1, "title": "Notion"}]})
    result = await client.fetch("wikipedia", query="notion")
    assert result.ok and result.data["count"] == 1

    router.add_json("page/summary/Notion", {"title": "Notion", "extract": "App"})
    summary = await client.fetch("wikipedia", title="Notion", language="en")
    assert summary.ok and summary.data["extract"] == "App"


async def test_wikidata_sparql(client, router) -> None:
    """SPARQL 结果 bindings 归一化提取。"""
    router.add_json("query.wikidata.org/sparql", {
        "head": {"vars": ["x"]},
        "results": {"bindings": [{"x": {"value": "Q1"}}]},
    })
    result = await client.fetch("wikidata", sparql="SELECT ?x WHERE {}")
    assert result.ok and result.data["bindings"][0]["x"]["value"] == "Q1"


async def test_gdelt_passthrough(client, router) -> None:
    """GDELT 响应原样透传。"""
    router.add_json("api.gdeltproject.org", {"articles": [{"url": "u"}]})
    result = await client.fetch("gdelt", query="notion")
    assert result.ok and result.data["articles"][0]["url"] == "u"


# ---- 网页获取/代理 --------------------------------------------------------------


@pytest.mark.parametrize("source", ["jina_reader", "allorigins", "codetabs", "corsproxy"])
async def test_web_fetch_rejects_non_http(client, source: str) -> None:
    """目标 URL 必须以 http(s):// 开头。"""
    with pytest.raises(ValueError):
        await client.fetch(source, url="ftp://example.com")


async def test_jina_reader_markdown(client, router) -> None:
    """Jina Reader 返回 Markdown 文本。"""
    router.add_text("r.jina.ai/", "# Title\n\nBody", content_type="text/plain")
    result = await client.fetch("jina_reader", url="https://example.com/x")
    assert result.ok and result.data.startswith("# Title")


async def test_proxy_json_passthrough(client, router) -> None:
    """内容代理对 JSON 目标返回解析后的对象。"""
    router.add_json("allorigins.win/raw", {"key": "value"})
    result = await client.fetch("allorigins", url="https://example.com/data.json")
    assert result.ok and result.data == {"key": "value"}
