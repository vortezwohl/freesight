"""Apple iTunes 系免费免 key 源:应用搜索、用户评论 RSS、各国榜单 RSS。

实测基准(2026-09-09):
- 单发延迟 0.3-0.5s;20 并发全过,边缘缓存热路径 QPS 实测 1333;
- 官方建议约 20 req/min,本模块按 50/min 保守节流(可按 429 观察上调);
- 三个源同属 itunes.apple.com,限流键默认按 host 共享同一预算。
"""

from __future__ import annotations

from typing import Any

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class ITunesSearchSource(BaseSource):
    """iTunes Search API:App Store 应用元数据搜索(名称/开发者/价格/图标等)。"""

    name = "itunes_search"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 50
    rate_period_s = 60
    cache_ttl_s = 3600.0
    search_kwarg = "term"
    search_default = True
    limit_kwarg = "limit"
    description = "App Store 应用元数据搜索,支持多国区与软件/音乐/电影等实体"

    async def fetch(
        self,
        term: str,
        country: str = "us",
        entity: str = "software",
        limit: int = 20,
    ) -> FetchResult:
        """按关键词搜索应用元数据。

        Args:
            term: 搜索关键词(应用名/关键词)。
            country: ISO 国家代码,决定商店国区(如 us/jp/cn)。
            entity: 搜索实体类型,应用研究固定 software。
            limit: 单页返回条数(上限 200)。

        Returns:
            data 为 {"resultCount": int, "results": [...]}。
        """
        return await self._get(
            "https://itunes.apple.com/search",
            params={"term": term, "country": country, "entity": entity, "limit": limit},
        )


class ITunesReviewsSource(BaseSource):
    """iTunes 评论 RSS:按应用+国区拉取最新用户评论(差评挖掘核心源)。

    注意两点:
    1. Apple 的 RSS 分页上限约 10 页 x 50 条;单条结果时 entry 为 dict
       而非 list,已在 _normalize 中统一为列表。
    2. 该接口为 Apple 老服务,存在服务端间歇性降级:2026-09-10 实测
       JSON/XML 多形态均返回 200 但 entry 为空(feed 结构不完整),
       同 URL 在 2026-09-09 曾正常返回 50 条/页。上层应把"空 reviews"
       视为可重试信号而非业务结论,并考虑 iTunes 查询接口的
       customerReviews 端点作为降级替代。
    """

    name = "itunes_reviews"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 50
    rate_period_s = 60
    cache_ttl_s = 900.0
    description = "App Store 用户评论(评分/标题/正文),按应用与国区分页拉取"

    async def fetch(
        self,
        app_id: int | str,
        country: str = "us",
        page: int = 1,
    ) -> FetchResult:
        """拉取某应用某国区一页评论。

        Args:
            app_id: Apple 应用数字 ID(bundle 数字 id,非包名字符串)。
            country: ISO 国家代码。
            page: 页码(1 起,约 10 页上限)。

        Returns:
            data 为 {"updated": str, "reviews": [{"rating","title","content","author"}, ...]}。
        """
        url = (
            f"https://itunes.apple.com/{country}/rss/customerreviews/"
            f"id={app_id}/sortby=mostrecent/page={page}/json"
        )
        result = await self._get(url)
        if result.ok:
            result.data = self._normalize(result.data)
        return result

    @staticmethod
    def _normalize(feed: Any) -> dict[str, Any]:
        """把 Apple RSS JSON 压平为评论列表,消除单条 dict 的坑。

        Args:
            feed: RSS 接口原始 JSON(feed 节点)。

        Returns:
            {"updated": 最后更新时间, "reviews": 评论列表}。
        """
        if not isinstance(feed, dict):
            return {"updated": "", "reviews": []}
        entries = feed.get("feed", {}).get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]

        def _label(node: dict, key: str) -> str:
            return node.get(key, {}).get("label", "")

        reviews = [
            {
                "rating": _label(e, "im:rating"),
                "title": _label(e, "title"),
                "content": _label(e, "content"),
                "author": _label(e.get("author", {}), "name"),
            }
            for e in entries
            if isinstance(e, dict)
        ]
        updated = feed.get("feed", {}).get("updated", {}).get("label", "")
        return {"updated": updated, "reviews": reviews}


class ITunesChartsSource(BaseSource):
    """App Store 榜单 RSS:各国区免费/付费/畅销榜。

    官方还可切 top paid applications / top grossing applications,
    通过 chart 参数透传。
    """

    name = "itunes_charts"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 50
    rate_period_s = 60
    cache_ttl_s = 3600.0
    description = "App Store 各国区榜单(免费/付费/畅销),单次最多 200 条"
    schema_overrides = {
        "chart": {
            "enum": ["topfreeapplications", "toppaidapplications", "topgrossingapplications"],
            "description": "榜单类型:免费榜/付费榜/畅销榜",
        },
    }

    async def fetch(
        self,
        country: str = "us",
        chart: str = "topfreeapplications",
        limit: int = 100,
        genre: int | None = None,
    ) -> FetchResult:
        """拉取某国区某榜单。

        Args:
            country: ISO 国家代码。
            chart: 榜单类型(topfreeapplications/toppaidapplications/topgrossingapplications)。
            limit: 条数上限 200。
            genre: 可选,App Store 分类 ID(如 6015 为游戏)。

        Returns:
            data 为 {"updated": str, "apps": [{"name","id","artist",...}, ...]}。
        """
        url = f"https://itunes.apple.com/{country}/rss/{chart}/limit={min(limit, 200)}/json"
        if genre is not None:
            url += f"/genre={genre}"
        result = await self._get(url)
        if result.ok:
            result.data = self._normalize(result.data)
        return result

    @staticmethod
    def _normalize(feed: Any) -> dict[str, Any]:
        """压平榜单 RSS 为应用列表。

        Args:
            feed: 榜单 RSS 原始 JSON。

        Returns:
            {"updated": 更新时间, "apps": 应用列表}。
        """
        if not isinstance(feed, dict):
            return {"updated": "", "apps": []}
        entries = feed.get("feed", {}).get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]

        def _label(node: dict, key: str) -> Any:
            return node.get(key, {}).get("label", "")

        def _app_id(entry: dict) -> str:
            # 榜单 RSS 的 id 节点:数字 app id 在 attributes.im:id,
            # 而 label 是字符串 URL——先判类型再取,避免对字符串调 get。
            node = entry.get("id", {})
            if isinstance(node, dict):
                return node.get("attributes", {}).get("im:id", "")
            return str(node)

        apps = [
            {
                "name": _label(e, "im:name"),
                "id": _app_id(e),
                # _label 语义是"从父节点取 key 子节点的 label",此处传 e 与字段名
                "artist": _label(e, "im:artist")
                if isinstance(e.get("im:artist"), dict)
                else str(e.get("im:artist", "")),
                "category": _label(e, "category")
                if isinstance(e.get("category"), dict) else str(e.get("category", "")),
            }
            for e in entries
            if isinstance(e, dict)
        ]
        return {"updated": _label(feed.get("feed", {}), "updated"), "apps": apps}

