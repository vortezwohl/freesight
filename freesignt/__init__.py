"""freesignt:免费竞品调研 SDK。

聚合 25 个免费免 key 公开数据源(App Store/HackerNews/GitHub/开发者
生态/招聘板/社媒/法定披露/Steam 等),提供:
- 人体工学客户端(同步 FreeSight / 异步 AsyncFreeSight,源名即方法);
- 多源扇出统一搜索(词法排序,可注入嵌入提供方升级为语义排序);
- C 端高并发防护(按 host 令牌桶限流 + 429 自适应冷却 + TTL 缓存 +
  单飞请求合并);
- Agent 工具封装(OpenAI function calling 格式,参数 schema 自动派生)。

SDK 不做持久化存储;缓存协议(CacheProtocol)可供调用方外接自有存储。

快速上手:
    import freesignt

    hits = freesignt.search("notion").hits[:10]

    with freesignt.FreeSight() as client:
        result = client.itunes_search(term="notion")
        tools = client.build_agent_tools()
"""

from freesignt import sources as _sources  # noqa: F401  (导入即完成全部源注册)
from freesignt.core.cache import CacheProtocol, NullCache, TTLCache
from freesignt.core.client import AsyncFreeSight, FreeSight
from freesignt.core.errors import FreeSightError, SourceNotFoundError
from freesignt.core.models import (
    FetchResult,
    Hit,
    SearchResponse,
    SourceCategory,
    SourceInfo,
)
from freesignt.core.registry import all_sources, by_category, names
from freesignt.core.search import EmbeddingProvider

__version__ = "0.1.0"

__all__ = [
    "AsyncFreeSight",
    "CacheProtocol",
    "FetchResult",
    "FreeSight",
    "FreeSightError",
    "Hit",
    "NullCache",
    "SearchResponse",
    "SourceCategory",
    "SourceFetchStatus",
    "SourceInfo",
    "SourceNotFoundError",
    "TTLCache",
    "EmbeddingProvider",
    "all_sources",
    "by_category",
    "names",
    "search",
    "fetch",
    "list_sources",
]

# 模块级默认同步客户端:支撑 freesignt.search("notion") 一行式用法。
_default_client: FreeSight | None = None


def _get_default_client() -> FreeSight:
    """惰性创建进程级默认同步客户端(单例)。"""
    global _default_client
    if _default_client is None:
        _default_client = FreeSight()
    return _default_client


def search(
    query: str,
    *,
    sources: list[str] | None = None,
    limit_per_source: int = 5,
    semantic: bool = True,
    embedder: "EmbeddingProvider | None" = None,
    refresh: bool = False,
) -> SearchResponse:
    """模块级快捷搜索:使用进程级默认客户端。

    Args:
        query: 查询词。
        sources: 可选参与源列表;None 用默认扇出集合。
        limit_per_source: 每源条数上限。
        semantic: 注入 embedder 时是否语义排序。
        embedder: 嵌入提供方(默认用默认客户端的配置,未配置则词法)。
        refresh: 是否绕过缓存。

    Returns:
        SearchResponse。
    """
    return _get_default_client().search(
        query,
        sources=sources,
        limit_per_source=limit_per_source,
        semantic=semantic,
        embedder=embedder,
        refresh=refresh,
    )


def fetch(name: str, /, **params: object) -> FetchResult:
    """模块级快捷取数:使用进程级默认客户端。

    Args:
        name: 源名称。
        **params: 源业务参数(refresh/ttl 为保留字)。

    Returns:
        FetchResult。
    """
    return _get_default_client().fetch(name, **params)  # type: ignore[arg-type]


def list_sources() -> list[SourceInfo]:
    """模块级源目录快捷方法。"""
    return _get_default_client().list_sources()
