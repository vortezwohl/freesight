"""社媒/社区免费免 key 源:Bluesky、Mastodon trends、V2EX。

实测基准(2026-09-09):
- Bluesky public API 无限速头(1.3s);
- Mastodon trends 可匿名,但 public timeline 已要求认证(实测 422);
  trends 限速头 300 次/5min;
- V2EX 限速头直读 600/h。
"""

from __future__ import annotations

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory


class BlueskySource(BaseSource):
    """Bluesky 公共 API(XRPC):账号/帖子检索。"""

    name = "bluesky"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60
    cache_ttl_s = 600.0
    search_kwarg = "q"
    search_default = True
    limit_kwarg = "limit"
    schema_overrides = {
        "method": {"enum": ["actor_search", "post_search"]},
    }
    description = "Bluesky 公共检索(账号搜索/帖子全文检索/趋势)"

    async def fetch(self, q: str, method: str = "actor_search", limit: int = 10) -> FetchResult:
        """按关键词检索 Bluesky 账号或帖子。

        Args:
            q: 搜索词。
            method: actor_search(账号,走 searchActors)
                /post_search(帖子,走 searchPosts)。
            limit: 返回条数。

        Returns:
            data 为 XRPC 返回 dict(actors/posts 节点)。

        Raises:
            ValueError: method 不合法。
        """
        base = "https://public.api.bsky.app/xrpc/"
        if method == "actor_search":
            url, params = base + "app.bsky.actor.searchActors", {"q": q, "limit": limit}
        elif method == "post_search":
            url, params = base + "app.bsky.feed.searchPosts", {"q": q, "limit": limit}
        else:
            raise ValueError(f"不支持的检索类型: {method}")
        return await self._get(url, params=params)


class MastodonTrendsSource(BaseSource):
    """Mastodon(mastodon.social)趋势标签与热门帖子(匿名可用部分)。"""

    name = "mastodon_trends"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 30
    rate_period_s = 60  # 实测限速头 300/5min
    cache_ttl_s = 600.0
    schema_overrides = {
        "kind": {"enum": ["tags", "statuses", "links"]},
    }
    description = "Mastodon 趋势标签/帖子/链接(public timeline 需认证,故只做 trends)"

    async def fetch(self, kind: str = "tags", limit: int = 20) -> FetchResult:
        """拉取趋势内容。

        Args:
            kind: tags(标签)/statuses(帖子)/links(链接)。
            limit: 条数(上限 40)。

        Returns:
            data 为趋势列表(list[dict])。

        Raises:
            ValueError: kind 不合法。
        """
        if kind not in ("tags", "statuses", "links"):
            raise ValueError(f"不支持的趋势类型: {kind}")
        url = f"https://mastodon.social/api/v1/trends/{kind}"
        return await self._get(url, params={"limit": min(limit, 40)})


class V2exSource(BaseSource):
    """V2EX 开放 API:热帖/最新帖/节点(国内独立开发者一手动态)。"""

    name = "v2ex"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 60
    rate_period_s = 60  # 实测限速头 600/h,取 60/min 保守值
    cache_ttl_s = 300.0
    schema_overrides = {
        "kind": {"enum": ["hot", "latest"]},
    }
    description = "V2EX 热帖/最新主题(分享创造节点是国产独立产品发布地)"

    async def fetch(self, kind: str = "hot") -> FetchResult:
        """拉取帖子列表。

        Args:
            kind: hot(热帖)/latest(最新)。

        Returns:
            data 为主题列表(list[dict],含标题/节点/回复数)。

        Raises:
            ValueError: kind 不合法。
        """
        if kind == "hot":
            url = "https://www.v2ex.com/api/topics/hot.json"
        elif kind == "latest":
            url = "https://www.v2ex.com/api/topics/latest.json"
        else:
            raise ValueError(f"不支持的列表类型: {kind}")
        return await self._get(url)

