"""学术文献免费免 key 源(2026-09 实测)。

- crossref: 1.5 亿+ DOI 文献元数据(免 key;mailto 可进 polite pool);
- openalex: 2.5 亿+ 学术实体开放索引(基础免 key;官方建议带
  mailto 提升限额);
- arxiv: 预印本检索(Atom XML,源内压平为 feed 条目)。
"""

from __future__ import annotations

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory
from freesignt.sources._feed import parse_feed


class CrossrefSource(BaseSource):
    """Crossref REST API:DOI/文献元数据与引用关系检索。"""

    name = "crossref"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 20
    rate_period_s = 60  # 免 key 档;mailto 进入 polite pool 后更稳
    timeout = 30
    cache_ttl_s = 21600.0
    description = "Crossref 文献元数据检索(1.5 亿+ DOI,mailto 礼貌池)"

    async def fetch(self, query: str, rows: int = 20, mailto: str = "") -> FetchResult:
        """检索 Crossref 文献。

        Args:
            query: 检索词(支持 field 限定如 "title:attention")。
            rows: 返回条数(上限 100)。
            mailto: 可选,联系邮箱(进入官方 polite pool,提升限额)。

        Returns:
            data 为 {"total", "items": [...], "query"}(message 结构裁剪);

        Raises:
            ValueError: query 为空。
        """
        if not query:
            raise ValueError("query 不能为空")
        params: dict[str, object] = {"query": query, "rows": min(rows, 100)}
        if mailto:
            params["mailto"] = mailto
        result = await self._get("https://api.crossref.org/works", params=params)
        if not result.ok or not isinstance(result.data, dict):
            return result
        message = result.data.get("message") or {}
        items = message.get("items") or []
        result.data = {
            "total": message.get("total-results", len(items)),
            "items": items,
            "query": query,
        }
        return result


class OpenAlexSource(BaseSource):
    """OpenAlex API:开放学术图谱(论文/作者/机构/期刊实体检索)。"""

    name = "openalex"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 20
    rate_period_s = 60  # 免 key 基础档;官方建议 mailto 提升
    timeout = 30
    cache_ttl_s = 21600.0
    schema_overrides = {
        "entity": {"enum": ["works", "authors", "sources", "institutions", "topics"]},
    }
    description = "OpenAlex 学术图谱检索(2.5 亿+ 实体,mailto 可提额)"

    async def fetch(self, search: str, entity: str = "works",
                    limit: int = 20, mailto: str = "") -> FetchResult:
        """检索 OpenAlex 学术实体。

        Args:
            search: 检索词。
            entity: 实体类型 works/authors/sources/institutions/topics。
            limit: 每页条数(上限 200)。
            mailto: 可选,联系邮箱(官方礼貌标识,提升限额)。

        Returns:
            data 为 {"total", "results": [...], "entity"};

        Raises:
            ValueError: search 为空或 entity 非法。
        """
        if not search:
            raise ValueError("search 不能为空")
        if entity not in ("works", "authors", "sources", "institutions", "topics"):
            raise ValueError(f"不支持的实体类型: {entity}")
        params: dict[str, object] = {"search": search, "per-page": min(limit, 200)}
        if mailto:
            params["mailto"] = mailto
        result = await self._get(f"https://api.openalex.org/{entity}", params=params)
        if not result.ok or not isinstance(result.data, dict):
            return result
        results = result.data.get("results") or []
        meta = result.data.get("meta") or {}
        result.data = {
            "total": meta.get("count", len(results)),
            "results": results,
            "entity": entity,
        }
        return result


class ArxivSource(BaseSource):
    """arXiv API:跨学科预印本检索(Atom XML 输出)。"""

    name = "arxiv"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 6
    rate_period_s = 60  # 官方建议每 3 秒 1 次并带间隔
    timeout = 30
    cache_ttl_s = 21600.0
    description = "arXiv 预印本检索(Atom XML,官方建议 1 次/3 秒)"

    async def fetch(self, search_query: str, max_results: int = 20,
                    sort: str = "relevance") -> FetchResult:
        """检索 arXiv 预印本。

        Args:
            search_query: 检索式(如 all:transformer 或
                ti:"large language model" AND cat:cs.CL)。
            max_results: 返回条数。
            sort: 排序(relevance/submittedDate/lastUpdatedDate)。

        Returns:
            data 为 parse_feed 结构(title/items,条目含标题/链接/摘要);

        Raises:
            ValueError: search_query 为空。
        """
        if not search_query:
            raise ValueError("search_query 不能为空")
        result = await self._get(
            "http://export.arxiv.org/api/query",
            params={
                "search_query": search_query,
                "max_results": max_results,
                "sortBy": sort,
                "sortOrder": "descending",
            },
        )
        if not result.ok or not isinstance(result.data, str):
            return result
        feed = parse_feed(result.data)
        if feed is None:
            return FetchResult(
                ok=False, status=result.status, latency_s=result.latency_s,
                error="arXiv 响应不是可解析的 Atom 文档", source=self.name,
            )
        result.data = feed
        return result
