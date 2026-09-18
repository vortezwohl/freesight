"""统一数据模型:结果、搜索命中、聚合响应与源元信息。

本模块是 freesignt 的公共数据契约层,不依赖任何 IO 设施:
- FetchResult: 单次数据获取的统一返回结构(兼容原 Industry-Research 形态,
  在此基础上增加 cached / fetched_at / meta 观测字段);
- Hit: 统一搜索归一化后的单条结果(跨源可比);
- SearchResponse: 多源扇出聚合后的整体响应;
- SourceInfo: 源的静态元信息(供人类查阅与 agent 工具生成)。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SourceCategory(StrEnum):
    """数据源获取方式分类,沿用原信息源地图的层级约定。

    FREE_NOKEY 为当前已实现分类;FREE_KEY / CRAWLER 为预留扩展位,
    子类落地后按类别自动归组。
    """

    FREE_NOKEY = "free_nokey"
    FREE_KEY = "free_key"
    CRAWLER = "crawler"


@dataclass
class FetchResult:
    """单次数据获取的统一返回结构。

    所有数据源的 fetch() 与缓存层都产出本结构;失败不抛异常(参数校验类
    ValueError 除外),由调用方按 ok 字段分流,便于高并发下的部分失败容忍。

    Attributes:
        ok: 请求是否成功(HTTP 200 且解析无致命错误)。
        status: HTTP 状态码;网络层异常时为 None。
        latency_s: 单次网络请求耗时(秒);命中缓存时为 0。
        data: 解析后的业务数据(通常为 dict/list);失败时为 None。
        error: 失败时的错误描述(异常名+信息或 HTTP 错误),成功时为 None。
        source: 产生本结果的源名称,便于上层追溯。
        meta: 观测元信息(重试次数、限速冷却、响应头摘要等)。
        cached: 本结果是否来自内存缓存(或单飞共享)。
        fetched_at: 底层网络请求完成的墙钟时间戳(time.time(),秒)。
    """

    ok: bool
    status: int | None
    latency_s: float
    data: Any = None
    error: str | None = None
    source: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    cached: bool = False
    fetched_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """导出为可直接 JSON 序列化的 dict(agent 工具输出用)。

        Returns:
            与字段一一对应的 dict;data 原样透传,由调用方决定裁剪策略。
        """
        return {
            "ok": self.ok,
            "status": self.status,
            "latency_s": self.latency_s,
            "data": self.data,
            "error": self.error,
            "source": self.source,
            "meta": self.meta,
            "cached": self.cached,
            "fetched_at": self.fetched_at,
        }


@dataclass
class Hit:
    """统一搜索的单条归一化结果。

    各数据源通过 BaseSource.to_hits() 把原始响应映射为本结构,使跨源
    结果可以在同一列表中排序与比较;raw 保留原始条目供深挖。

    Attributes:
        source: 来源源名称(如 itunes_search)。
        title: 展示标题(应用名/帖子标题/仓库名等)。
        url: 可跳转链接(没有链接的源为空字符串)。
        snippet: 一句话摘要(供语义排序与展示)。
        score: 排序得分(词法或语义,越高越相关)。
        extra: 源特有结构化字段(评分/下载量/星数等)。
        raw: 原始条目数据(只读约定,调用方不应修改)。
    """

    source: str
    title: str = ""
    url: str = ""
    snippet: str = ""
    score: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)
    raw: Any = None

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        """导出 dict。

        Args:
            include_raw: 是否包含原始条目(数据量大,agent 输出默认裁剪)。

        Returns:
            可 JSON 序列化的 dict。
        """
        out = {
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "score": round(self.score, 4),
            "extra": self.extra,
        }
        if include_raw:
            out["raw"] = self.raw
        return out


@dataclass
class SourceFetchStatus:
    """统一搜索中单个源的执行状态(成功/失败/缓存均不中断整体)。"""

    ok: bool
    count: int = 0
    error: str | None = None
    cached: bool = False
    latency_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """导出 dict。"""
        return {
            "ok": self.ok,
            "count": self.count,
            "error": self.error,
            "cached": self.cached,
            "latency_s": round(self.latency_s, 4),
        }


@dataclass
class SearchResponse:
    """多源扇出聚合搜索的整体响应。

    Attributes:
        query: 原始查询词。
        hits: 归一化并按 score 降序排列的命中列表。
        semantic: 本次排序是否使用了语义嵌入(否则为词法排序)。
        took_s: 整体耗时(秒,含网络)。
        per_source: {源名: SourceFetchStatus},含失败源的降级信息。
    """

    query: str
    hits: list[Hit] = field(default_factory=list)
    semantic: bool = False
    took_s: float = 0.0
    per_source: dict[str, SourceFetchStatus] = field(default_factory=dict)

    @property
    def total(self) -> int:
        """命中总数。"""
        return len(self.hits)

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        """导出 dict(agent 工具输出用)。

        Args:
            include_raw: 是否在每条命中中保留原始条目。

        Returns:
            可 JSON 序列化的 dict。
        """
        return {
            "query": self.query,
            "semantic": self.semantic,
            "total": self.total,
            "took_s": round(self.took_s, 4),
            "hits": [h.to_dict(include_raw=include_raw) for h in self.hits],
            "per_source": {k: v.to_dict() for k, v in self.per_source.items()},
        }


@dataclass
class SourceInfo:
    """源的静态元信息(注册表导出给人看,也用于生成 agent 工具描述)。

    Attributes:
        name: 源唯一名称。
        category: 获取方式分类。
        description: 一句话中文说明。
        rate_limit: 限速窗口内请求数(实测快照)。
        rate_period_s: 限速窗口长度(秒)。
        timeout: 单请求超时秒数。
        max_retries: 网络层异常最大重试次数。
        cache_ttl_s: 建议缓存 TTL(秒);0 表示不缓存。
        searchable: 是否可参与统一搜索(有可用的关键词参数)。
        search_default: 是否进入默认搜索扇出集合。
        input_schema: fetch 参数的 JSON Schema。
        doc: fetch 的完整中文 docstring。
    """

    name: str
    category: SourceCategory
    description: str = ""
    rate_limit: int = 60
    rate_period_s: int = 60
    timeout: int = 20
    max_retries: int = 2
    cache_ttl_s: float = 600.0
    searchable: bool = False
    search_default: bool = False
    input_schema: dict[str, Any] = field(default_factory=dict)
    doc: str = ""

    def to_dict(self) -> dict[str, Any]:
        """导出 dict。"""
        return {
            "name": self.name,
            "category": self.category.value,
            "description": self.description,
            "rate_limit": self.rate_limit,
            "rate_period_s": self.rate_period_s,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "cache_ttl_s": self.cache_ttl_s,
            "searchable": self.searchable,
            "search_default": self.search_default,
            "input_schema": self.input_schema,
        }
