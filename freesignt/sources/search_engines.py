"""通用搜索引擎/元搜索/知识库免费免 key 源(2026-09 实测)。

本模块覆盖"全网检索入口"类渠道:
- ddg_search: ddgs 库(浏览器指纹模拟)封装 DuckDuckGo,文本/新闻;
- searxng: 开源元搜索公共实例(优先 JSON,被禁时回退解析 HTML);
- baidu / yahoo / mojeek: 三个可直接 GET 的 SERP 网页(源内正则压平);
- wikipedia: 官方 REST 搜索与页面摘要;
- wikidata: SPARQL 查询端点(Wikidata 要求申明式 UA);
- gdelt: GDELT 2.0 Doc API 全球新闻文章索引(实测 1 次/5 秒)。

实测备注(2026-09-20):
- 百度 SERP 匿名直连可能命中"百度安全验证"页,源内显式识别为失败;
- searx.be 实测 format=json 被禁(返回 HTML),故设计为双形态回退;
- baidu/yahoo/mojeek 的 SERP 解析为尽力而为,页面改版时返回原始 HTML。
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
import time
from typing import Any

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


def _strip_tags(fragment: str) -> str:
    """去掉 SERP 片段内的标签并反转义实体(标题/摘要清洗共用)。"""
    return html_lib.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _search_results(html: str, pattern: re.Pattern[str]) -> list[dict[str, str]]:
    """按正则从 SERP HTML 提取 (url, title) 结果并清洗。"""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for url, title in pattern.findall(html):
        title = _strip_tags(title)
        if not url or not title or url in seen:
            continue
        seen.add(url)
        out.append({"title": title, "url": html_lib.unescape(url)})
    return out


class DdgSearchSource(BaseSource):
    """DuckDuckGo 搜索(ddgs 库封装):文本/新闻双模式。

    走 ddgs 库(primp 浏览器指纹模拟)而非本 SDK 引擎,因此限流/重试
    由库内节流兜底;源内用 asyncio.to_thread 包装同步调用,不阻塞
    事件循环。
    """

    name = "ddg_search"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 20
    rate_period_s = 60
    timeout = 25
    cache_ttl_s = 1800.0
    schema_overrides = {
        "kind": {"enum": ["text", "news"]},
    }
    description = "DuckDuckGo 网页/新闻搜索(ddgs 库,浏览器指纹模拟)"

    async def fetch(self, query: str, max_results: int = 10,
                    region: str = "wt-wt", kind: str = "text") -> FetchResult:
        """执行一次 DuckDuckGo 搜索。

        Args:
            query: 搜索词。
            max_results: 返回条数上限。
            region: 地区代码(如 wt-wt 全球 / us-en / zh-cn)。
            kind: text(网页)/news(新闻)。

        Returns:
            data 为 {"results": [{"title", "href", "body"}, ...], "count": int};
            ddgs 内部异常(限流/网络)耗尽时 ok=False。

        Raises:
            ValueError: query 为空或 kind 不合法。
        """
        if not query:
            raise ValueError("query 不能为空")
        if kind not in ("text", "news"):
            raise ValueError(f"不支持的检索类型: {kind}")

        def _run() -> list[dict[str, Any]]:
            from ddgs import DDGS

            d = DDGS(timeout=self.timeout)
            if kind == "news":
                return list(d.news(query, region=region, max_results=max_results))
            return list(d.text(query, region=region, max_results=max_results))

        start = time.perf_counter()
        try:
            hits = await asyncio.to_thread(_run)
        except Exception as exc:  # ddgs 抛出的库级异常统一转失败结果
            return FetchResult(
                ok=False, status=None, latency_s=time.perf_counter() - start,
                error=f"ddgs 检索失败: {type(exc).__name__}: {exc}", source=self.name,
            )
        latency = time.perf_counter() - start
        return FetchResult(
            ok=True, status=200, latency_s=latency, source=self.name,
            data={"results": hits, "count": len(hits)},
        )


class SearXngSource(BaseSource):
    """SearXNG 元搜索:聚合 70+ 引擎的开源实例。

    公共实例(如 searx.be)默认禁用 JSON 输出,源内优先请求 format=json,
    收到 HTML 时回退解析结果块;实例需调用方经 instance 参数自选或自建
    (目录见 searx.space)。
    """

    name = "searxng"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 10
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 900.0
    description = "SearXNG 元搜索公共实例(JSON 优先,HTML 回退解析)"

    _HTML_HIT = re.compile(
        r'<h3[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', re.S
    )

    async def fetch(self, query: str, instance: str = "https://searx.be",
                    limit: int = 10, categories: str = "general") -> FetchResult:
        """在指定 SearXNG 实例上执行搜索。

        Args:
            query: 搜索词。
            instance: 实例基址(公共实例不稳定,生产建议自建)。
            limit: 返回条数上限。
            categories: 搜索类别(general/images/news/it/science 等)。

        Returns:
            data 为 {"results": [{"title", "url", "engines"?}, ...], "count": int}。

        Raises:
            ValueError: query 为空。
        """
        if not query:
            raise ValueError("query 不能为空")
        url = f"{instance.rstrip('/')}/search"
        result = await self._get(
            url, params={"q": query, "format": "json", "categories": categories}
        )
        if not result.ok:
            return result
        if isinstance(result.data, dict):
            raw = result.data.get("results") or []
            results = [
                {"title": r.get("title"), "url": r.get("url"), "engines": r.get("engines")}
                for r in raw[:limit] if isinstance(r, dict)
            ]
            result.data = {"results": results, "count": len(results)}
            return result
        # JSON 被实例禁用:_parse_body 回退为 HTML 文本;先识别反爬页。
        if isinstance(result.data, str) and (
            "Verifying your browser" in result.data or "antibot" in result.data
        ):
            return FetchResult(
                ok=False, status=result.status, latency_s=result.latency_s,
                error="实例启用了浏览器验证(反爬),请更换实例或自建",
                source=self.name,
            )
        hits = _search_results(result.data, self._HTML_HIT)[:limit]
        if not hits:
            return FetchResult(
                ok=False, status=result.status, latency_s=result.latency_s,
                error="实例未开启 JSON 输出且 HTML 解析无结果,请更换实例或自建",
                source=self.name,
            )
        result.data = {"results": hits, "count": len(hits), "fallback": "html"}
        return result


class BaiduSearchSource(BaseSource):
    """百度网页搜索:匿名 GET SERP,源内正则压平结果。

    百度无免 key API;匿名高频会命中"百度安全验证"页,
    源内显式识别为失败并提示降频。
    """

    name = "baidu"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 6
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 1800.0
    description = "百度网页搜索(SERP 正则解析,易触发安全验证)"

    _HIT = re.compile(r'<h3[^>]*>\s*<a[^>]+href="(http[^"]+)"[^>]*>(.*?)</a>', re.S)

    async def fetch(self, query: str, limit: int = 10) -> FetchResult:
        """执行一次百度搜索。

        Args:
            query: 搜索词。
            limit: 返回条数上限(百度 rn 参数,上限约 50)。

        Returns:
            data 为 {"results": [{"title", "url"}, ...], "count": int};
            命中安全验证页时 ok=False;解析无结果时返回原始 HTML 文本。

        Raises:
            ValueError: query 为空。
        """
        if not query:
            raise ValueError("query 不能为空")
        result = await self._get(
            "https://www.baidu.com/s", params={"wd": query, "rn": limit}
        )
        if not result.ok or not isinstance(result.data, str):
            return result
        if "百度安全验证" in result.data:
            return FetchResult(
                ok=False, status=result.status, latency_s=result.latency_s,
                error="触发百度安全验证:请降低频率或更换出口 IP",
                source=self.name,
            )
        hits = _search_results(result.data, self._HIT)[:limit]
        if hits:
            result.data = {"results": hits, "count": len(hits)}
        return result


class YahooSearchSource(BaseSource):
    """Yahoo 网页搜索:匿名 GET SERP,源内正则压平结果。

    结果块真实形态(2026-09 实测):标题在 h3.title 内的 span 中,
    链接 href 在更外层的 a 标签上(a 与 h3 之间隔着 favicon 块),
    故采用"h3 定位 + 向前窗口找最近 href"的两段式解析。
    """

    name = "yahoo"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 10
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 1800.0
    description = "Yahoo 网页搜索(SERP 正则解析)"

    _TITLE = re.compile(r'<h3[^>]*class="title fc-[^"]*"[^>]*>(.*?)</h3>', re.S)
    _HREF = re.compile(r'href="(https?://[^"]+)"')

    async def fetch(self, query: str, limit: int = 10) -> FetchResult:
        """执行一次 Yahoo 搜索。

        Args:
            query: 搜索词。
            limit: 返回条数上限。

        Returns:
            data 为 {"results": [{"title", "url"}, ...], "count": int};
            解析无结果时返回原始 HTML 文本由调用方处理。

        Raises:
            ValueError: query 为空。
        """
        if not query:
            raise ValueError("query 不能为空")
        result = await self._get("https://search.yahoo.com/search", params={"p": query})
        if not result.ok or not isinstance(result.data, str):
            return result
        out: list[dict[str, str]] = []
        for m in self._TITLE.finditer(result.data):
            title = _strip_tags(m.group(1))
            if not title:
                continue
            # 外层 a 标签可能距 h3 数百字符(favicon 块),向前找最近 href。
            window = result.data[max(0, m.start() - 800):m.start()]
            hrefs = self._HREF.findall(window)
            url = html_lib.unescape(hrefs[-1]) if hrefs else ""
            if url and "search.yahoo.com/search" not in url:
                out.append({"title": title, "url": url})
        if out:
            result.data = {"results": out[:limit], "count": len(out[:limit])}
        return result


class MojeekSearchSource(BaseSource):
    """Mojeek 独立索引搜索引擎:匿名 GET SERP。

    2026-09 实测:httpx 直连返回无结果的空壳页(疑似服务端客户端
    检测),需浏览器化会话;源保留官方公开端点与解析逻辑,
    解析无结果时返回原始 HTML 由调用方判断。
    """

    name = "mojeek"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 10
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 1800.0
    description = "Mojeek 独立索引网页搜索(httpx 直连实测返回空壳,需浏览器会话)"

    _HIT = re.compile(r'<h2[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', re.S)

    async def fetch(self, query: str, limit: int = 10) -> FetchResult:
        """执行一次 Mojeek 搜索。

        Args:
            query: 搜索词。
            limit: 返回条数上限。

        Returns:
            data 为 {"results": [{"title", "url"}, ...], "count": int};
            解析无结果时返回原始 HTML 文本由调用方处理。

        Raises:
            ValueError: query 为空。
        """
        if not query:
            raise ValueError("query 不能为空")
        result = await self._get("https://www.mojeek.com/search", params={"q": query})
        if not result.ok or not isinstance(result.data, str):
            return result
        hits = _search_results(result.data, self._HIT)[:limit]
        if hits:
            result.data = {"results": hits, "count": len(hits)}
        return result


class WikipediaSource(BaseSource):
    """维基百科官方 REST API:全文搜索与页面摘要(280+ 语言版本)。"""

    name = "wikipedia"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60
    cache_ttl_s = 3600.0
    description = "维基百科 REST 搜索/页面摘要(多语言)"

    async def fetch(self, query: str = "", language: str = "en", limit: int = 10,
                    title: str | None = None) -> FetchResult:
        """搜索维基百科或取页面摘要。

        Args:
            query: 搜索词(与 title 二选一)。
            language: 语言版本代码(如 en/zh/ja)。
            limit: 搜索返回条数上限。
            title: 可选,页面标题(提供时改为返回该页 REST 摘要)。

        Returns:
            搜索模式 data 为 {"pages": [...], "count": int};
            摘要模式 data 为摘要对象(title/extract/content_urls 等)。

        Raises:
            ValueError: query 与 title 均为空。
        """
        if title:
            return await self._get(
                f"https://{language}.wikipedia.org/api/rest_v1/page/summary/"
                f"{str(title).strip().replace(' ', '_')}"
            )
        if not query:
            raise ValueError("query 与 title 至少提供一个")
        result = await self._get(
            f"https://{language}.wikipedia.org/w/rest.php/v1/search/page",
            params={"q": query, "limit": limit},
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        pages = result.data.get("pages") or []
        result.data = {"pages": pages, "count": len(pages)}
        return result


class WikidataSparqlSource(BaseSource):
    """Wikidata SPARQL 查询端点:结构化知识图谱检索(Wikidata 要求 UA)。"""

    name = "wikidata"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 5
    rate_period_s = 60  # 官方建议并发 1、间隔执行;SPARQL 服务有查询限额
    timeout = 30
    cache_ttl_s = 3600.0
    description = "Wikidata SPARQL 查询(结构化知识图谱,限速保守)"

    async def fetch(self, sparql: str) -> FetchResult:
        """执行一次 SPARQL 查询。

        Args:
            sparql: 完整 SPARQL 查询语句(SELECT/ASK 形态)。

        Returns:
            data 为 {"head": ..., "bindings": [...]};服务端查询错误时 ok=False。

        Raises:
            ValueError: sparql 为空。
        """
        if not sparql:
            raise ValueError("sparql 不能为空")
        result = await self._get(
            "https://query.wikidata.org/sparql",
            params={"query": sparql, "format": "json"},
        )
        if not result.ok or not isinstance(result.data, dict):
            return result
        bindings = (result.data.get("results") or {}).get("bindings") or []
        result.data = {"head": result.data.get("head"), "bindings": bindings}
        return result


class GdeltDocSource(BaseSource):
    """GDELT 2.0 Doc API:全球新闻文章全文索引(实测 1 次/5 秒)。"""

    name = "gdelt"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 6
    rate_period_s = 60  # 实测 429 提示 "one every 5 seconds",取保守 6/min
    timeout = 30
    cache_ttl_s = 1800.0
    description = "GDELT 2.0 全球新闻文章索引(1 次/5 秒限速)"

    async def fetch(self, query: str, timespan: str = "1w", limit: int = 30,
                    mode: str = "artlist") -> FetchResult:
        """检索全球新闻文章。

        Args:
            query: GDELT 查询语法(支持布尔/域名限定如 "notion site:techcrunch.com")。
            timespan: 时间窗(如 1d/1w/3m);或起止日期 "20260101000000,20260901000000"。
            limit: maxrecords 上限(服务端硬顶 250)。
            mode: artlist(文章清单)/timelinevol(逐日量)。

        Returns:
            data 为响应 dict(articles 字段为文章数组);429 时 ok=False。

        Raises:
            ValueError: query 为空。
        """
        if not query:
            raise ValueError("query 不能为空")
        return await self._get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": query, "mode": mode, "format": "json",
                "timespan": timespan, "maxrecords": limit,
            },
        )
