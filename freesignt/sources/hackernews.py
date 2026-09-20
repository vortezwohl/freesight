"""Hacker News 免费免 key 源:Algolia 全历史搜索与 Firebase 实时接口。

实测基准(2026-09-09):
- Algolia 单发约 1.1s,20 并发全过(QPS 16,延迟主导非限速);
- Firebase 同样宽松;两者均无认证要求。
"""

from __future__ import annotations

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class HackerNewsAlgoliaSource(BaseSource):
    """HN Algolia 搜索:2007 年至今全部帖子/评论,Show HN 挖掘的主力源。"""

    name = "hn_algolia"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60
    timeout = 25
    cache_ttl_s = 600.0
    schema_overrides = {
        "tags": {
            "description": "标签过滤:story(帖子)/show_hn/comment/ask_hn,多值逗号分隔",
        },
    }
    description = "Hacker News 全历史搜索(Show HN/Ask HN/评论),支持时间范围与标签过滤"

    async def fetch(
        self,
        query: str,
        tags: str = "story",
        hits_per_page: int = 30,
        page: int = 0,
    ) -> FetchResult:
        """按关键词搜索 HN 内容。

        Args:
            query: 搜索词;支持 Algolia 语法(引号精确/前缀*)。
            tags: 标签过滤,如 story/show_hn/comment/ask_hn,多值逗号分隔。
            hits_per_page: 每页条数(上限 1000)。
            page: 页码(0 起)。

        Returns:
            data 为 {"nbHits": 总数, "page": 页码, "hits": [...]}。
        """
        return await self._get(
            "https://hn.algolia.com/api/v1/search",
            params={
                "query": query,
                "tags": tags,
                "hitsPerPage": hits_per_page,
                "page": page,
            },
        )


class HackerNewsFirebaseSource(BaseSource):
    """HN Firebase 实时接口:热帖/新帖 ID 列表与单条 item 明细(浏览型源)。"""

    name = "hn_firebase"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60
    cache_ttl_s = 300.0
    schema_overrides = {
        "kind": {"enum": ["topstories", "newstories", "beststories"]},
    }
    description = "Hacker News 实时数据(top/new/best 帖子 ID 与单条内容)"

    async def fetch(self, kind: str = "topstories", item_id: int | None = None) -> FetchResult:
        """拉取故事 ID 列表或单条 item 详情。

        Args:
            kind: 列表类型 topstories/newstories/beststories;当提供
                item_id 时忽略本参数。
            item_id: 可选,具体帖子/评论 ID(取详情时传入)。

        Returns:
            data 为 ID 列表(list[int])或单条 item dict。
        """
        if item_id is not None:
            url = f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
        else:
            url = f"https://hacker-news.firebaseio.com/v0/{kind}.json"
        return await self._get(url)

