"""社媒/内容流免费免 key 源(2026-09 实测与行为核对)。

- reddit: 子版块 json/rss 双形态(2026-06 起匿名限速收紧至约
  10 qpm,源内取保守 6/min);
- youtube_rss: 频道/播放列表官方 RSS(免 key);
- google_news: Google News RSS 检索/定向(免 key,支持语言/地区);
- rsshub: RSSHub 公共实例路由(中文生态核心桥梁:微博/B站/知乎/
  公众号等,公共实例易被上游反爬,生产建议自建);
- lobsters: Lobsters 技术社区 JSON(lobste.rs,公开端点)。

内容流类源的条目解析由 sources/_feed.parse_feed 统一承担
(RSS 2.0 与 Atom 双形态)。
"""

from __future__ import annotations

from freesignt.core.base import BaseSource
from freesignt.core.models import FetchResult, SourceCategory
from freesignt.sources._feed import parse_feed


def _as_feed(result: FetchResult, limit: int) -> FetchResult:
    """把成功返回的 XML 文本解析为 feed 条目结构(失败转源失败)。"""
    if not result.ok or not isinstance(result.data, str):
        return result
    feed = parse_feed(result.data)
    if feed is None:
        return FetchResult(
            ok=False, status=result.status, latency_s=result.latency_s,
            error="响应不是可解析的 RSS/Atom 文档", source=result.source,
        )
    if limit > 0:
        feed["items"] = feed["items"][:limit]
        feed["count"] = len(feed["items"])
    result.data = feed
    return result


class RedditSource(BaseSource):
    """Reddit 公开读取:子版块列表的 json/rss 双形态。

    2026-06 起 Reddit 对匿名流量限速显著收紧(约 10 qpm,全 feed
    共享配额);本源取保守 6/min。重度使用应注册免费 OAuth 应用
    (不属免 key 范畴)。
    """

    name = "reddit"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 6
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 300.0
    schema_overrides = {
        "listing": {"enum": ["hot", "new", "top", "rising"]},
        "fmt": {"enum": ["json", "rss"]},
    }
    description = "Reddit 子版块公开读取(json/rss,匿名限速严格)"

    async def fetch(self, subreddit: str = "all", listing: str = "hot",
                    limit: int = 25, fmt: str = "json") -> FetchResult:
        """读取一个子版块的帖子列表。

        Args:
            subreddit: 子版块名(all 为全站聚合)。
            listing: 排序维度 hot/new/top/rising。
            limit: 条数上限。
            fmt: json(结构化 children)/rss(feed 条目)。

        Returns:
            json 模式 data 为 {"posts": [...], "before", "after"};
            rss 模式 data 为 parse_feed 结构;

        Raises:
            ValueError: subreddit/listing/fmt 非法。
        """
        if not subreddit:
            raise ValueError("subreddit 不能为空")
        if listing not in ("hot", "new", "top", "rising"):
            raise ValueError(f"不支持的排序: {listing}")
        if fmt not in ("json", "rss"):
            raise ValueError(f"不支持的格式: {fmt}")
        suffix = "json" if fmt == "json" else "rss"
        result = await self._get(
            f"https://www.reddit.com/r/{subreddit}/{listing}.{suffix}",
            params={"limit": limit},
        )
        if fmt == "rss":
            return _as_feed(result, limit)
        if not result.ok or not isinstance(result.data, dict):
            return result
        payload = result.data.get("data") or {}
        children = payload.get("children") or []
        result.data = {
            "posts": [c.get("data") for c in children if isinstance(c, dict)],
            "before": payload.get("before"),
            "after": payload.get("after"),
        }
        return result


class YoutubeRssSource(BaseSource):
    """YouTube 频道/播放列表官方 RSS:免 key 的更新流监控通道。"""

    name = "youtube_rss"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    cache_ttl_s = 900.0
    description = "YouTube 频道/播放列表 RSS(免 key 官方更新流)"

    async def fetch(self, channel_id: str | None = None,
                    playlist_id: str | None = None, limit: int = 15) -> FetchResult:
        """读取一个频道或播放列表的最新视频清单。

        Args:
            channel_id: 可选,频道 UC 开头 ID(页面源码可搜 channel_id)。
            playlist_id: 可选,播放列表 ID(优先于 channel_id)。
            limit: 条数上限(官方每份 feed 约 15 条)。

        Returns:
            data 为 parse_feed 结构(title/items,含视频标题与链接);

        Raises:
            ValueError: channel_id 与 playlist_id 均未提供。
        """
        params: dict[str, object] = {}
        if playlist_id:
            params["playlist_id"] = playlist_id
        elif channel_id:
            params["channel_id"] = channel_id
        else:
            raise ValueError("channel_id 与 playlist_id 至少提供一个")
        result = await self._get(
            "https://www.youtube.com/feeds/videos.xml", params=params
        )
        return _as_feed(result, limit)


class GoogleNewsSource(BaseSource):
    """Google News RSS:新闻检索与定向监控(免 key,多语言/地区)。"""

    name = "google_news"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    cache_ttl_s = 900.0
    description = "Google News RSS 检索(支持 site: 定向与多语言地区)"

    async def fetch(self, query: str, hl: str = "en-US", gl: str = "US",
                    ceid: str = "US:en", limit: int = 20) -> FetchResult:
        """按关键词检索新闻。

        Args:
            query: 检索词(支持 site:example.com 定向单一媒体)。
            hl: 界面语言(如 en-US / zh-CN / ja-JP)。
            gl: 地区代码(如 US / CN / JP)。
            ceid: 国家:语言(与 hl/gl 配套,如 CN:zh-Hans)。
            limit: 条数上限。

        Returns:
            data 为 parse_feed 结构(注意条目链接为 Google 跳转编码,
            需原始链接时由调用方解码);

        Raises:
            ValueError: query 为空。
        """
        if not query:
            raise ValueError("query 不能为空")
        result = await self._get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": hl, "gl": gl, "ceid": ceid},
        )
        return _as_feed(result, limit)


class RsshubSource(BaseSource):
    """RSSHub 路由读取:把微博/B站/知乎/公众号等转成标准 RSS。

    公共实例(rsshub.app)易被上游反爬,可用性波动大;生产建议
    Docker 自建实例并经 instance 参数指定。
    """

    name = "rsshub"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 10
    rate_period_s = 60
    timeout = 30
    cache_ttl_s = 600.0
    description = "RSSHub 路由读取(中文社媒生态桥梁,实例可自定)"

    async def fetch(self, route: str, instance: str = "https://rsshub.app",
                    limit: int = 20) -> FetchResult:
        """读取一条 RSSHub 路由。

        Args:
            route: 路由路径,以 / 开头(如 /weibo/user/1195230310;
                完整路由表见 docs.rsshub.app)。
            instance: 实例基址(自建实例填自有地址)。
            limit: 条数上限。

        Returns:
            data 为 parse_feed 结构;路由不存在/实例被反爬时 ok=False。

        Raises:
            ValueError: route 不以 / 开头。
        """
        if not route.startswith("/"):
            raise ValueError("route 必须以 / 开头(如 /weibo/user/xxx)")
        result = await self._get(f"{instance.rstrip('/')}{route}")
        return _as_feed(result, limit)


class LobstersSource(BaseSource):
    """Lobsters 技术社区:热帖/最新帖 JSON 公开端点。"""

    name = "lobsters"
    category = SourceCategory.FREE_NOKEY
    rate_limit = 15
    rate_period_s = 60
    cache_ttl_s = 300.0
    schema_overrides = {
        "category": {"enum": ["hottest", "newest", "recent"]},
    }
    description = "Lobsters 技术社区帖子清单(JSON)"

    async def fetch(self, category: str = "hottest", limit: int = 20) -> FetchResult:
        """读取 Lobsters 帖子清单。

        Args:
            category: hottest(热)/newest(最新提交)/recent(最近活跃)。
            limit: 条数上限。

        Returns:
            data 为 {"stories": [...], "count": int}(官方数组裁剪);

        Raises:
            ValueError: category 非法。
        """
        if category not in ("hottest", "newest", "recent"):
            raise ValueError(f"不支持的清单类型: {category}")
        result = await self._get(f"https://lobste.rs/{category}.json")
        if not result.ok or not isinstance(result.data, list):
            return result
        stories = result.data[:limit]
        result.data = {"stories": stories, "count": len(stories)}
        return result
